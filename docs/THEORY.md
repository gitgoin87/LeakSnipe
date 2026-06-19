# Game Theory Tooling (CFR+ & Value Networks)

LeakSnipe includes **educational** game-theory tooling — not a Pluribus-class solver.
This document explains what CFR+ solves here, what the neural net approximates, and
what is realistically runnable on a laptop.

## Scope & honesty

| Capability | In LeakSnipe | Full NLHE solver |
|---|---|---|
| Kuhn / Leduc toy games | ✅ CFR+ | ✅ |
| Abstracted push/fold spot | ✅ CFR+ | ✅ |
| Full NLHE equilibrium | ❌ | ✅ (with abstraction) |
| Real-time in-game advice | ❌ | ❌ (even pros use precomputed) |
| Monte Carlo equity | ✅ `equity.py` | ✅ |
| NN value estimate | ✅ lightweight MLP | ✅ (Deep CFR, etc.) |

Full no-limit hold'em has ~10^160 information sets. Practical solvers use:

- **Card abstraction** (bucketing hands by equity / EHS)
- **Action abstraction** (discrete bet sizes)
- **Subgame solving** (solve a river subtree offline)
- **Neural networks** to approximate counterfactual values or strategies (Deep CFR, Single Deep CFR, ReBeL)

LeakSnipe implements CFR+ on **small exact games** plus a **numpy MLP** trained on MC equity samples.

## CFR+ algorithm

**Counterfactual Regret Minimization (CFR)** iteratively minimizes regret per information set.
**CFR+** (Tammelin et al.) floors regrets at zero and uses regret-matching+ ; average
strategy converges toward Nash equilibrium in two-player zero-sum games.

Our implementation (`theory/cfr_solver.py`):

- Regret matching+ with strategy averaging
- Chance nodes for card dealing
- Exploitability estimate via best-response computation
- Subgames: `kuhn`, `leduc`, `push_fold`

### Kuhn poker (primary test)

3-card deck {J,Q,K}, antes, one betting round. Known Nash qualitative strategy:

- **P0**: bet K always; bluff Q at ~33%; check J always
- **P1**: call K; call Q at ~33% vs bet; fold J

### Leduc Hold'em

6-card deck, private + public card, two limit rounds. Slower to converge — use 5k+ iterations.

### Push/fold abstraction

HU preflop with 3 hand-strength buckets per player. Demonstrates CFR+ on a poker-shaped
spot without enumerating 1,326 combos.

**Limitation:** Charts and push/fold CFR+ spots are **heads-up only**. In multi-way pots
(3+ players or facing a bet with callers), use the app's `pot_odds.py` multi-way formula
(`to_call / (pot_including_callers + to_call)`) — do not apply HU chart frequencies blindly.

## Neural value function

`theory/value_net.py` implements a **2-layer ReLU MLP** (numpy by default; PyTorch optional).

| Input | Output |
|---|---|
| Hero cards (52 one-hot) | Equity / EV fraction [0,1] |
| Board cards (52 one-hot) | |
| Pot odds, position, street | |

**Training data**: Monte Carlo equity from `equity.py` vs a wide range.

**Weights**: `models/value_net.npz` (gitignored). Train via UI or `POST /api/theory/value/train`.

The NN does **not** replace the solver — it approximates value on sampled spots, similar in
spirit to value networks in Deep CFR but at toy scale.

## Architecture in LeakSnipe

```
leaksnipe-ui (Theory tab)
    ↓ REST
sidecar/server.py  —  /api/theory/*
    ↓
theory/cfr_solver.py   — CFR+ on toy subgames
theory/value_net.py    — MLP trained on equity.py MC samples
equity.py              — ground-truth equity for training & coach
ai_processor.py        — coach tools: run_cfr_solver, predict_value
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/theory/games` | List solvable subgames |
| POST | `/api/theory/cfr` | Run CFR+ `{game, iterations}` |
| POST | `/api/theory/value` | NN value prediction |
| POST | `/api/theory/value/train` | Train weights on MC samples |

## Python libraries evaluated

| Library | pip install | Notes |
|---|---|---|
| **Custom (chosen)** | — | Zero extra deps, fits LeakSnipe style |
| OpenSpiel | `pip install open_spiel` | Excellent research tool; heavy build on Windows |
| PokerRL | GitHub | RL-focused, PyTorch, overkill for coach hints |
| PyPokerEngine | pip | Engine only, no CFR |
| pgx / ray.rllib | pip | Research-scale |

We use a **minimal in-repo CFR+** to avoid Windows install pain and keep the sidecar lightweight.

## What runs locally on a laptop

| Task | Time | Feasible |
|---|---|---|
| Kuhn CFR+ 10k iters | <1s | ✅ |
| Leduc CFR+ 5k iters | few sec | ✅ |
| Push/fold CFR+ | <1s | ✅ |
| Value net train (300 samples) | ~30–60s | ✅ |
| Full NLHE river solve (all combos) | hours+ | ❌ without abstraction |
| Deep CFR training | GPU hours | ❌ (not implemented) |

## User setup

```bash
# From repo root
pip install -r sidecar/requirements.txt   # adds numpy
pip install -e .

# Restart sidecar / Launch-LeakSnipe.bat
```

Open **Theory** tab in the desktop app:

1. **Run CFR+** on Kuhn/Leduc/push-fold — view strategy table & exploitability
2. **Train model** once — then **Predict value** for any hero/board
3. Ask the **AI Coach** about theory — it can call `run_cfr_solver` and `predict_value`

## Future extensions (not in scope now)

- River subgame solver with card bucketing from `equity.py`
- Train value net on CFR+ counterfactual values (not just MC equity)
- Export strategy charts to hand replayer overlay
- Optional OpenSpiel bridge for researchers
