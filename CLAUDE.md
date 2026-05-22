# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Thermal** is an RL-based fail-slow mitigation framework for manycore accelerators. It uses PPO and DQN to optimize task remapping on a 4x4 mesh NoC (Network-on-Chip) to mitigate performance degradation from thermally-induced slowdowns.

## Commands

**Environment setup:**
```bash
conda create --name thermal-rl --file environment.yml
conda activate thermal-rl
```

**Training:**
```bash
python3 rl_agent/train_ppo.py [--num-cpu 8] [--total-timesteps 10000]
python3 rl_agent/train_dqn.py
```

**Evaluation:**
```bash
python3 rl_agent/evaluate_policy.py --algo ppo --model <checkpoint>
python3 scripts/validate_local_remap.py
```

**Tests:**
```bash
python3 -m pytest tests/test_local_remap.py        # unit tests
python3 rl_agent/train_test.py                      # RL environment smoke tests
python3 test.py                                     # PyTorch/Gymnasium smoke test
```

## Architecture

### Key Layers

**RL Agent (`rl_agent/`)** — training scripts and Gymnasium environment
- `envs/rl_env.py`: `FailSlowEnv` — wraps the simulator as a Gymnasium env; actions are MultiDiscrete `[layer_id, src_core, dst_core, op_type]` for local remap operations (replace, split, shift, remove)
- `envs/PPOagent.py`: `CustomFeatureExtractor` — GCN encodes hardware topology, combined with runtime stats via CNN to produce 256-dim latent features
- `reward/reward_function.py`: `RewardCalculator` — multi-objective reward weighting performance (α=2.0) and thermal balance (β_core=1.0, β_link=0.5)

**Simulator (`simulator/`)** — discrete-event simulation via SimPy; **not modified by RL logic**
- `run.py`: orchestration — loads configs, drives simulation
- `architecture.py`: `Arch` — top-level, owns cores and NoC
- `core.py`: task execution and scratchpad memory
- `noc.py`: routers and links

**Utils (`utils/`)**
- `mapper.py`: `NetworkMapper` — the central piece for task mapping; implements and certifies all 4 local remap actions; ~1500 LOC
- `definitions.py`: shared data structures (`Trace`, `TimeSlice`, `OperatorType`, etc.)
- `dfg.py`: Data Flow Graph representation

**Configs (`configs/`)**
- `schemas/`: Pydantic models for arch, mapping, and failure configs
- `instances/gemini4_4.json`: default 4x4 mesh hardware config
- `instances/normal.json`: no-failure baseline config

**Workloads / Data**
- `workloads/darknet19-4-4.json`: default DarkNet-19 mapping for 4x4 mesh
- `fail_dataset/*.json`: 100+ fail-slow scenarios used for training

### Default Workflow

The RL loop runs multiple parallel `FailSlowEnv` instances (`SubprocVecEnv`). Each step: the agent observes hardware graph + runtime stats → produces a remap action → `NetworkMapper` certifies and applies it → simulator runs → reward computed from cycle reduction + thermal balance.

### Important Notes

- `scripts/train.py` and `scripts/evaluate.py` are **not maintained** — use `rl_agent/train_ppo.py` instead.
- Local remap certification lives in `utils/mapper.py`; the simulator under `simulator/` is intentionally left unchanged.
- Successful simulation runs write artifacts: `trace.json`, `core.json`, `link.json`, `dfg.txt`.
- TensorBoard logs land in `rl_agent/logs/`; model checkpoints in `rl_agent/models/`.
