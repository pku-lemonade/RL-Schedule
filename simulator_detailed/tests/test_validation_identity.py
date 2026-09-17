"""Identity tests use temporary Git repositories and independent byte digests."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.configs.schemas.validation import (
    ArtifactReference,
    RunIdentity,
    SeedState,
)
from simulator_detailed.tests.validation_fixtures import identity_document
from simulator_detailed.validation.identity import (
    collect_environment,
    collect_run_identity,
    collect_source_identity,
    content_digest,
    read_verified_artifact,
    resolve_asset,
)


class ValidationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "a.py").write_bytes(b"rate = 4\n")
        (self.root / "src" / "z.py").write_bytes(b"width = 16\n")
        (self.root / "workload.json").write_bytes(b'{"size":64}')
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Validation fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "synthetic fixture")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def collect(self, root=None, **changes):
        root = root or self.root
        return collect_run_identity(root, **{
            "inputs": {"workload.json": root / "workload.json"}, "effective_plan": {"size": 64, "rate": 4},
            "selection": {"adapter": "compute_workload_v1", "checks": ["drain"]},
            "seed": SeedState(mode="deterministic"), "source_roots": ("src",), "dependencies": (),
            **changes,
        })

    def test_clean_source_manifest_has_sorted_exact_bytes_and_independent_digest(self):
        source = collect_source_identity(self.root, source_roots=("src",))
        self.assertFalse(source.dirty.value)
        self.assertEqual(source.revision.value, self.git("rev-parse", "HEAD").strip())
        expected = [{"logical_path": name, "sha256": hashlib.sha256((self.root / name).read_bytes()).hexdigest(),
                     "size_bytes": len((self.root / name).read_bytes())} for name in ("src/a.py", "src/z.py")]
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(source.bundle_sha256, digest)

    def test_same_git_revision_with_dirty_source_bytes_is_distinct(self):
        before, _ = self.collect()
        (self.root / "src" / "a.py").write_bytes(b"rate = 8\n")
        after, _ = self.collect()
        self.assertEqual(before.source.revision, after.source.revision)
        self.assertNotEqual(before.source.bundle_sha256, after.source.bundle_sha256)
        self.assertTrue(after.source.dirty.value)
        self.assertNotEqual(content_digest(before), content_digest(after))

    def test_staged_untracked_and_deleted_python_sources_are_recorded(self):
        before, _ = self.collect()
        new = self.root / "src" / "new.py"
        new.write_bytes(b"capacity = 2\n")
        added, _ = self.collect()
        self.assertNotEqual(before.source.bundle_sha256, added.source.bundle_sha256)
        self.assertTrue(added.source.dirty.value)
        self.assertIn("src/new.py", [f.logical_path for f in added.source.files])
        self.git("add", "src/new.py")
        staged, _ = self.collect()
        self.assertEqual(staged.source.bundle_sha256, added.source.bundle_sha256)
        self.assertTrue(staged.source.dirty.value)
        (self.root / "src" / "a.py").unlink()
        deleted, _ = self.collect()
        tombstone = next(f for f in deleted.source.files if f.logical_path == "src/a.py")
        self.assertIsNone(tombstone.sha256)
        self.assertIsNone(tombstone.size_bytes)
        self.assertNotEqual(staged.source.bundle_sha256, deleted.source.bundle_sha256)

    def test_relocation_timestamps_and_file_mtime_do_not_change_portable_identity(self):
        first, first_diagnostic = self.collect(recorded_at="2026-09-17T00:00:00Z")
        second_root = Path(self.temp.name) / "another-machine"
        shutil.copytree(self.root, second_root)
        os.utime(second_root / "src" / "a.py", (0, 0))
        second, second_diagnostic = self.collect(second_root, recorded_at="2030-01-01T00:00:00Z")
        self.assertEqual(first, second)
        self.assertEqual(content_digest(first), content_digest(second))
        self.assertNotEqual(first_diagnostic, second_diagnostic)

    def test_output_never_hashes_itself_even_when_under_source_root(self):
        output = self.root / "src" / "report.py"
        first, _ = self.collect(outputs=(output,))
        output.write_bytes(b"generated report content")
        second, _ = self.collect(outputs=(output,))
        output.write_bytes(b"different report with its own digest")
        third, _ = self.collect(outputs=(output,))
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        with self.assertRaisesRegex(ValueError, "output cannot be an identity input"):
            self.collect(outputs=(self.root / "workload.json",))

    def test_unrelated_docs_do_not_dirty_selected_source_identity(self):
        first, _ = self.collect()
        (self.root / "notes.md").write_text("unrelated local notes")
        (self.root / "src" / "result.json").write_text("generated report")
        second, _ = self.collect()
        self.assertEqual(first.source, second.source)

    def test_raw_bytes_effective_plan_selections_and_seed_are_distinct_identities(self):
        first, _ = self.collect()
        (self.root / "workload.json").write_bytes(b'{ "size": 64 }\n')
        whitespace, _ = self.collect()
        self.assertNotEqual(first.inputs, whitespace.inputs)
        self.assertEqual(first.effective_plan_sha256, whitespace.effective_plan_sha256)
        changed_plan, _ = self.collect(effective_plan={"size": 64, "rate": 8})
        changed_checks, _ = self.collect(selection={"adapter": "compute_workload_v1", "checks": ["ownership"]})
        seeded, _ = self.collect(seed=SeedState(mode="seeded", seed=17))
        self.assertNotEqual(whitespace.effective_plan_sha256, changed_plan.effective_plan_sha256)
        self.assertNotEqual(whitespace.selection_sha256, changed_checks.selection_sha256)
        self.assertNotEqual(content_digest(whitespace), content_digest(seeded))

    def test_reference_bytes_are_verified_without_replacing_sidecar_hash(self):
        sidecar = self.root / "refs" / "reference.json"
        sidecar.parent.mkdir()
        raw = sidecar.parent / "capture.bin"
        raw.write_bytes(b"exact captured bytes\x00")
        artifact = ArtifactReference(path="capture.bin", sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
        self.assertEqual(resolve_asset(sidecar, artifact.path), raw)
        self.assertEqual(read_verified_artifact(sidecar, artifact), raw.read_bytes())
        expected_hash = artifact.sha256
        raw.write_bytes(b"changed capture")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            read_verified_artifact(sidecar, artifact)
        self.assertEqual(artifact.sha256, expected_hash)
        raw.unlink()
        with self.assertRaises(FileNotFoundError):
            read_verified_artifact(sidecar, artifact)

    def test_nested_assets_resolve_from_each_declaring_document_outside_cwd(self):
        outer = self.root / "suite.json"
        inner = resolve_asset(outer, "refs/sidecar.json")
        self.assertEqual(resolve_asset(inner, "../workload.json"), self.root / "workload.json")
        old_cwd = Path.cwd()
        try:
            os.chdir(self.temp.name)
            self.assertEqual(resolve_asset(inner, "../workload.json"), self.root / "workload.json")
        finally:
            os.chdir(old_cwd)

    def test_unavailable_git_and_dependencies_stay_unknown(self):
        with patch("simulator_detailed.validation.identity._git", side_effect=FileNotFoundError):
            source = collect_source_identity(self.root, source_roots=("src",))
        self.assertEqual(source.revision.state, "unknown")
        self.assertEqual(source.dirty.state, "unknown")
        self.assertEqual(len(source.files), 2)
        env = collect_environment(("simpy", "definitely-absent-validation-fixture"))
        self.assertEqual(env.dependencies[0].version.state, "unknown")
        self.assertEqual(env.dependencies[1].version.value, "4.1.2")
        with self.assertRaisesRegex(ValueError, "duplicate normalized"):
            collect_environment(("pydantic_core", "pydantic-core"))

    def test_empty_escaping_and_duplicate_source_roots_are_rejected(self):
        for roots in ((), ("src", "src"), ("../outside",), ("/absolute",), ("src/../src",), ("missing",)):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                collect_source_identity(self.root, source_roots=roots)
        outside = Path(self.temp.name) / "outside.py"
        outside.write_text("external source")
        (self.root / "src" / "escape.py").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "escapes checkout"):
            collect_source_identity(self.root, source_roots=("src",))

    def test_seed_modes_and_forged_identity_content_are_rejected(self):
        for arguments in ({"mode": "seeded"}, {"mode": "deterministic", "seed": 1},
                          {"mode": "not_applicable", "seed": 1}, {"mode": "seeded", "seed": True}):
            with self.assertRaises(ValidationError):
                SeedState(**arguments)
        for field in ("effective_plan_sha256", "selection_sha256"):
            doc = identity_document()
            doc[field] = "c" * 64
            with self.assertRaisesRegex(ValidationError, "hash does not match"):
                RunIdentity.model_validate_json(json.dumps(doc))
        doc = identity_document()
        doc["source"]["files"][0]["sha256"] = "c" * 64
        with self.assertRaisesRegex(ValidationError, "bundle hash"):
            RunIdentity.model_validate_json(json.dumps(doc))
