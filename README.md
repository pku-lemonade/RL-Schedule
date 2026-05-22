# Thermal

Thermal is an experimental framework for fail-slow mitigation on manycore accelerators. It combines a manycore simulator, a GNN-based fail-slow detector, and an RL mitigation agent that remaps DNN workload partitions away from degraded cores.

The current workflow focuses on 4x4 mesh accelerators and detector-informed local remap actions.

## Repository Layout

- `simulator/`: manycore architecture, NoC, core, and trace generation logic.
- `utils/`: DFG, task, tensor-slice, and mapping/remap utilities.
- `predictor/`: GNN fail-slow detector and prediction entrypoints.
- `rl_agent/`: RL environments, PPO/DQN training, policy evaluation, and overhead experiments.
- `baselines/`: baseline evaluation scripts for no mitigation, simple mitigation, and RL without fail-slow probabilities.
- `configs/`: architecture, failure, and mapping schemas plus hardware instances.
- `workloads/`: 4x4 workload mappings for Darknet19, GoogLeNet, ResNet50, and VGG.
- `fail-slow/`: fail-slow datasets grouped by spatial pattern.
- `tests/`: focused regression tests for local remap behavior.
- `scripts/`: validation and debugging helpers.

## Setup

The project is developed with Python 3.12. A Conda environment specification is provided:

```bash
conda env create -f environment.yml
conda activate thermal-rl
```

If you install manually, the main packages are:

```bash
pip install numpy scipy simpy pydantic gymnasium stable-baselines3 torch torch-geometric
```

For CUDA-enabled PyG wheels, install the matching packages for your PyTorch/CUDA version. For example, with PyTorch 2.5 and CUDA 12.1:

```bash
pip install pyg_lib torch_scatter torch_sparse torch_cluster -f https://data.pyg.org/whl/torch-2.5.0+cu121.html
```

## Default Configuration

Default paths are centralized in `rl_agent/workflow_config.py`.

- Hardware: `configs/instances/gemini4_4.json`
- Workload: `workloads/darknet19-4-4.json`
- Fail-slow dataset root: `fail-slow/`
- Detector checkpoint: `models/best_model.pth`

Large generated files, logs, trained RL checkpoints, and temporary simulator outputs are ignored by Git. If you want to publish trained models with a release, attach them as GitHub release assets instead of committing them directly.

## Validate The Mapper

Run the local remap validation script:

```bash
python -m scripts.validate_local_remap
```

Run the regression tests:

```bash
python -m unittest tests.test_local_remap
```

## Train The RL Agent

Train PPO:

```bash
python -m rl_agent.train_ppo --num-cpu 4 --total-timesteps 10000
```

Train DQN:

```bash
python -m rl_agent.train_dqn
```

Training outputs are written under `rl_agent/models/` and `rl_agent/logs/`, which are ignored by Git.

## Evaluate A Trained Policy

Evaluate a single fixed fail-slow case:

```bash
python -m rl_agent.evaluate_policy \
  --algo ppo \
  --model rl_agent/models/ppo_checkpoints/<run>/best_model.zip \
  --fail fail-slow/center/fail1.json \
  --deterministic \
  --max-steps 50 \
  --output logs/eval/fail1.json
```

Evaluate a continuous-inference scenario where the mapping persists across requests:

```bash
python -m rl_agent.evaluate_continuous \
  --algo ppo \
  --model rl_agent/models/best_model.zip \
  --fail fail-slow/center/fail1.json \
  --times 10 \
  --deterministic \
  --output logs/eval/continuous_fail1.json
```

## Baseline Experiments

Run the no-mitigation and simple-mitigation baselines across a dataset pattern:

```bash
python -m baselines.run_suite \
  --root fail-slow \
  --patterns center \
  --methods no_mitigation simple_mitigation \
  --times 10 \
  --limit 100 \
  --jobs 32 \
  --max-request-cycles 1000000000 \
  --output-dir ../data/rl-result/baselines/center_times10
```

Run the same baselines over selected workloads:

```bash
python -m baselines.run_workload_suite \
  --patterns center \
  --methods no_mitigation simple_mitigation \
  --workloads workloads/vgg-4-4.json \
  --times 10 \
  --limit 100 \
  --jobs 32 \
  --max-request-cycles 1000000000 \
  --output-dir ../data/rl-result/baselines/vgg_center_times10
```

Train or evaluate the ablation baseline that removes fail-slow probabilities from the RL state:

```bash
python -m baselines.rl_without_fail_prob --mode train --num-cpu 4 --total-timesteps 10000
```

## Overhead Experiments

Measure runtime overhead for the trained RL policy:

```bash
python -m rl_agent.evaluate_overhead \
  --algo ppo \
  --model rl_agent/models/best_model.zip \
  --fail fail-slow/center/fail1.json \
  --times 10 \
  --deterministic \
  --output ../data/rl-result/overhead/fail1_overhead.json
```

Run an aggregated overhead suite:

```bash
python -m rl_agent.run_overhead_suite \
  --algo ppo \
  --model rl_agent/models/best_model.zip \
  --patterns center \
  --times 10 \
  --jobs 4 \
  --deterministic \
  --output-dir ../data/rl-result/overhead/center_times10
```

## Notes For Publication

- The simulator source under `simulator/` is intentionally kept independent from the RL mitigation logic.
- Local remap action application is implemented in `utils/mapper.py`.
- Generated logs and experiment outputs are not versioned by default.
- Before publishing, review whether `fail-slow/`, `workloads/`, and detector checkpoints should be included in the public repository or provided separately as artifacts.
