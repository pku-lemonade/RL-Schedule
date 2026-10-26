# Public MoE model and design-space exploration

The public model lives in `simulator_detailed/moe_model`. Its source-bound
contracts, calibration schemas, workload accounting, resource composition and
exploration pipeline share the generic simulator runtime. The required FIFO,
multicast, quantity-resource, cleanup and operation-envelope dependencies are
included; no separate runtime checkout or overlay path is required. These
extensions are opt-in. Existing graph validation, scheduling, operation-cost
compilation and legacy result formats retain the repository's core behavior.

See [DESIGN_SPACE.md](../../DESIGN_SPACE.md) for the caller-owned design-space
model, strict JSON contracts, uncertainty semantics and limitations. Design-space
evaluation consumes only the supplied model and request. It does not run the
execution pipeline or discover calibration artifacts.

## Usage

Run from the repository root. Standalone design-space commands require only the
Python standard library:

```sh
python simulator_detailed/moe_model/design_space.py schema --kind model --mode hypothetical_joint
python simulator_detailed/moe_model/design_space.py schema --kind request --mode evidence_portfolio
python simulator_detailed/moe_model/design_space.py catalog --model model.json
python simulator_detailed/moe_model/design_space.py template --model model.json
python simulator_detailed/moe_model/design_space.py evaluate --model model.json --request request.json
```

Package imports and the existing exploration CLI additionally use Pydantic 2
and SimPy 4, as listed in the project's dependency requirements. Use a project
environment rather than installing packages globally.

```sh
python -m simulator_detailed.moe_model.design_space schema --kind model --mode hypothetical_joint
python -c 'from simulator_detailed.moe_model.exploration_pipeline import main; main()' design-space schema --kind request --mode hypothetical_joint
```

The older execution/calibration commands still require their explicitly supplied
source and evidence contracts. Their historical artifact loaders are not a source
of public example inputs. No production evidence, device runner or private
adapter is distributed here. Source-exact constants in the accepted public
model describe bounded contracts, not defaults for arbitrary architectures.

## Public CPU Tests

```sh
python -m unittest discover -s tests -p 'test_*design*.py' -v
python -m unittest discover -s tests -p 'test_moe_*.py' -v
python -m unittest discover -s simulator_detailed/tests -v
```

The synthetic pipeline tests supply their own explicitly non-publishable
calibration fixture. They never discover historical measurements or exercise
native execution. A passing synthetic test is not physical-model validation.
