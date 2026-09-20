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
