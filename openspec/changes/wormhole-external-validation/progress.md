# Wormhole external validation progress

## Part 1 - Campaign contracts and fail-closed admission

Status: complete on 2026-09-20. Implementation started from
`72a12c4598c135c915a74d984e7fdf53ead4d876` on Python 3.12.12 with
Pydantic 2.13.5, Pyright 1.1.414 and Ruff 0.16.7. No producer process,
vendor tool or hardware device was invoked.

Delivered:

- Strict immutable `external_validation_campaign`, `external_capture_bundle`
  and `external_validation_report` version-1 records. They retain finite case,
  invocation, timeout and output budgets; immutable source/build identities;
  explicit conditions and boundary maps; exact integer counters; artifact
  lineage; known/unknown metadata; scoped outcomes and honest report status.
- Pure campaign and capture preflight. It resolves portable paths from the
  declaring document, verifies declared byte sizes and SHA-256 identities,
  matches capture producer/build identity to the admitted campaign, protects
  inputs from output aliases and never evaluates command text.
- Dependency-free legacy consumer rejection for campaign, capture and external
  report documents before Torch/PyG or checkpoint loading. Static compatibility
  checks retain 7-D predictor features, 4-D detailed hardware features, the
  `models/best_model.pth` checkpoint location and four-coordinate root actions.
- Valid, unavailable-environment and adversarial fixtures for the three initial
  case families. These fixtures are synthetic contract evidence only.

Pinned planning source identities:

- TT-Metal source revision:
  `a4e9bec4a5bcb4d7dc048a7cfed8122499d9ab2e`.
- ttsim source revision reserved by the design for later collection:
  `40bb1a2ad6a755279c4628ddc65e30b10721fdef`.
- The fixture build/source hashes are explicit synthetic identities and do not
  assert that either external project was built or run.

Fixture SHA-256 identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `8ecbd87dc5aaf9720368a3a8091e237014cb9ae2d8efec568375f6e977cf558c` |
| `campaign.adversarial.json` | `7383637d2e258e8b777fca89382149a05fc7ac08b1f166e2defb26c5d41899ac` |
| `capture.valid.json` | `57ce641fdec9fbc5937cac8bc6bcdada3467c79089dd17f3fe0bc2173caf73ca` |
| `capture.unavailable.json` | `0b525eef2115f62c9c8121706cb6c63e6b27041f3f4b34dedf9992eac5ec515d` |
| `capture.adversarial.json` | `b835cdc0ce13d3ce9ac1f4a07a1385a4992d13a4181e4c355cec6fec76f09541` |
| `report.blocked.json` | `9562badfc4f807ab0a0f0e49831ebdccc3d8e5702eb135e7d286d3e73068470a` |
| `inputs/noc_ack_roundtrip.json` | `4a28f188c343c2280993759d56d3fdeff0956dfb3eb86a306eff4e446b09a662` |
| `inputs/dram_read_return.json` | `8f03972b819ca9624b2d37667a58e401fb84ceda7f5b8ae976ab3802a8cd78c4` |
| `inputs/compute_service.json` | `dfe3e52b101bdcd6e101ba46ad2504ca27e89454a36158f6a19111af72b74d35` |
| `raw/functional_capture.json` | `4bf7a65c868859e2138f165f5806e29d64ec6eee4a48c803435f4349cf2de809` |

Implementation source SHA-256 identities before the progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/external_validation.py` | `c32469b9e56d36760c44905d3d9e31bd45938d56061d8044b3c88d82f2eab8ef` |
| `validation/external.py` | `7d0b160c8fd80f7f237ecf13d2fca5d17611c937af7774989478d89d6592fe35` |
| `topology_compatibility.py` | `2f4c198ac03395798221d14c7293d0bd08d89101a2430944d2c85ae59958ba85` |
| `tests/test_external_validation_contracts.py` | `c3c6f539c4736ba5ff4363c7b8495dff44e0f0fb787741ff7e8b839687950f12` |
| `pyrightconfig.phase2.json` | `7f93f41158de4ff8b6c89ad506c0fcec75e290d1089afc5491e613a1684801d9` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_cli simulator_detailed.tests.test_topology_consumers
67 tests passed in 17.513s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/external_validation.py simulator_detailed/configs/schemas/validation.py simulator_detailed/validation simulator_detailed/topology_compatibility.py simulator_detailed/tests/test_external_validation_contracts.py simulator_detailed/tests/test_validation_*.py simulator_detailed/tests/validation_fixtures.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```

Remaining external evidence: none of the fixtures is an admitted ttsim or
Wormhole execution. Parts 3 and 4 retain their explicit worker/device gates.

## Part 2 - Supported model intervals and comparison admission

Status: complete on 2026-09-20. Work started from Part 1 commit
`c4884ca`. No replay result contract, configured hardware value, external
producer or device environment was changed or invoked.

Delivered:

- Additive interval metrics derived from normalized source events for
  acknowledged operation completion, memory service and compute-resource
  service. Each metric retains its exact start/end event identities, subject,
  optional resource, ACI clock, completion scope and raw configured duration.
  Incomplete replay behavior remains compatible and raw replay outputs remain
  byte-for-byte independent of normalization.
- Typed campaign model-interval selectors and repetition policies with explicit
  repetition/warm-up identities and none/mean/median aggregation. Campaign
  admission ties each policy to its finite case budget and supported family.
- Deterministic model repetition execution that runs every declared repetition,
  excludes only named warm-ups, preserves per-run endpoint events, emits the
  matching retained sample policy and reports mean absolute deviation.
- A strict boundary registry for the legacy total-run window and the three
  source-local intervals. Comparison checks completion semantics, exact local
  identities, explicit cross-producer entity and clock maps, equal frequencies
  for cycles, and explicit conversion for seconds. Host dispatch, whole kernels,
  ambiguous intervals, cross-core subtraction and overhead-adjusted boundaries
  remain blocked.
- Profiler imports now retain endpoint identities for supported interval
  boundaries while preserving raw integer timestamps, selected runs, warm-ups
  and sample statistics.

Implementation source SHA-256 identities before the progress/task update:

| Source | SHA-256 |
| --- | --- |
| `configs/schemas/validation.py` | `39c3ca6afeae7d91d408a773473071a4b294b3f9b1be402573d428b36dad7756` |
| `configs/schemas/external_validation.py` | `b2513a9bd1c7283f2eb3f58e1653d7ad46660de1274e282de6eafed558c4a73f` |
| `validation/normalize.py` | `74a9e268a548075fbe4dc46cbaf36a87ed8ec8cff906d907588784238d0a2536` |
| `validation/mixed_normalize.py` | `4581a8fd00045d206743b09fa52149462624a335126751aa7b5064bc89df6e02` |
| `validation/intervals.py` | `ffa77a4621a6a4f8e6ff2d836f9cbbb4370b33001516e5c1811998ad2b2fbc9d` |
| `validation/comparison.py` | `a2ba74a896316166a4335bf7b53ca9d4e15a3c21f30cd609fe2e01ac52536352` |
| `validation/references.py` | `3444c75aa41f004725b92a2dad473e7c197008589ed73dd266eab941fc935a44` |
| `validation/external.py` | `d26b86144dde41c1cb45f8b3de31e327728164f4caf63a5c389c39f01380423d` |
| `tests/test_external_validation_intervals.py` | `027d1c0ae7f2c9c08aee39edd64169e084e6a716623db401f2b47f9a93e8cfce` |
| `tests/test_validation_references.py` | `15005fcf3ce4bc1a6eda1fb0cef408204cc65d12c40185e0ec489d08f04f408a` |

Revised fixture SHA-256 identities:

| Artifact | SHA-256 |
| --- | --- |
| `campaign.valid.json` | `072d7a47373f7f6cc12ffe5584e71b400ff4c08727353bfbcfe759bbb9ab9973` |
| `capture.valid.json` | `fafe6d1cc3728d661bcfce1659579eac6f7adb1f1b3ce516d31da5ad3a55fb6b` |
| `capture.unavailable.json` | `cf889771911f609af039c9f83b9cb57248a7809e17c844d441dde4ae9df93cfd` |
| `report.blocked.json` | `f39ecb785d70f2c48c841e1f0445c3032d0e50be4c2ce249e4cecb6d004ba36f` |

Executed verification:

```text
.venv/bin/python -m unittest simulator_detailed.tests.test_external_validation_intervals simulator_detailed.tests.test_validation_adapters simulator_detailed.tests.test_mixed_validation simulator_detailed.tests.test_validation_references simulator_detailed.tests.test_external_validation_contracts simulator_detailed.tests.test_validation_contracts simulator_detailed.tests.test_validation_identity simulator_detailed.tests.test_validation_runner simulator_detailed.tests.test_validation_cli
139 tests passed in 111.447s; 0 failures; 0 errors; 0 skips.

.venv/bin/python -m pyright --pythonpath .venv/bin/python --project simulator_detailed/pyrightconfig.phase2.json
0 errors, 0 warnings, 0 informations.

.venv/bin/ruff check simulator_detailed/configs/schemas/validation.py simulator_detailed/configs/schemas/external_validation.py simulator_detailed/validation simulator_detailed/tests/test_external_validation_intervals.py simulator_detailed/tests/test_validation_adapters.py simulator_detailed/tests/test_mixed_validation.py simulator_detailed/tests/test_validation_references.py simulator_detailed/tests/test_external_validation_contracts.py
All checks passed.

npm exec --yes --package=@fission-ai/openspec@1.11.0 -- openspec validate wormhole-external-validation --strict --no-interactive
Change 'wormhole-external-validation' is valid.

git diff --check
Passed with no output.
```
