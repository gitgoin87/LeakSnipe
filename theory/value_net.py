"""
Lightweight value-function approximation for poker spots.

Uses a numpy-only 2-layer MLP by default. PyTorch is optional (graceful fallback).
Trains on Monte Carlo equity samples from equity.py; can also ingest CFR+ labels.

Weights saved to models/value_net.npz (gitignored).
"""

from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import equity as equity_engine

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_PATH = os.path.join(_REPO_ROOT, "models", "value_net.npz")

TORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn

    TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore
    nn = None  # type: ignore

INPUT_DIM = 52 + 52 + 7  # hero + board + context (+ stack_bb)
HIDDEN = 64


def _normalize_bb(value: float, bb: float = 1000.0) -> float:
    bb = max(1.0, float(bb))
    return float(np.clip(value / bb / 20.0, 0.0, 1.0))


def _card_index(card: equity_engine.Card) -> int:
    rank, suit = card
    return (rank - 2) * 4 + suit


def encode_spot(
    hero: str,
    board: str = "",
    *,
    pot_odds: float = 0.33,
    position: float = 0.5,
    street: float = 0.0,
    ante_per_player: float = 0.0,
    dead_money: float = 0.0,
    bb: float = 1000.0,
    stack_bb: float = 25.0,
) -> np.ndarray:
    """Feature vector for a poker spot."""
    vec = np.zeros(INPUT_DIM, dtype=np.float32)
    hero_cards = equity_engine.parse_cards(hero)
    board_cards = equity_engine.parse_cards(board or "")
    for c in hero_cards[:2]:
        vec[_card_index(c)] = 1.0
    for c in board_cards[:5]:
        vec[52 + _card_index(c)] = 1.0
    vec[104] = float(np.clip(pot_odds, 0.0, 1.0))
    vec[105] = float(np.clip(position, 0.0, 1.0))
    vec[106] = float(np.clip(street, 0.0, 1.0))
    vec[107] = len(board_cards) / 5.0
    vec[108] = _normalize_bb(ante_per_player, bb)
    vec[109] = _normalize_bb(dead_money, bb)
    vec[110] = float(np.clip(stack_bb / 100.0, 0.0, 1.0))
    return vec


_WIDE_RANGE = "22+,A2s+,A2o+,KQo,KJo,QJo,JTo,T9s,98s,87s"


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(0, x)


def _mlp_forward(x: np.ndarray, w1: np.ndarray, b1: np.ndarray, w2: np.ndarray, b2: np.ndarray) -> np.ndarray:
    h = _relu(x @ w1 + b1)
    return h @ w2 + b2


class NumpyValueNet:
    """Simple 2-layer MLP stored as numpy arrays."""

    def __init__(self, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 0.1, (INPUT_DIM, HIDDEN)).astype(np.float32)
        self.b1 = np.zeros(HIDDEN, dtype=np.float32)
        self.w2 = rng.normal(0, 0.1, (HIDDEN, 1)).astype(np.float32)
        self.b2 = np.zeros(1, dtype=np.float32)

    def predict(self, x: np.ndarray) -> float:
        if x.ndim == 1:
            out = _mlp_forward(x, self.w1, self.b1, self.w2, self.b2)
            return float(np.clip(out[0], 0.0, 1.0))
        out = _mlp_forward(x, self.w1, self.b1, self.w2, self.b2).reshape(-1)
        return float(np.clip(out.mean(), 0.0, 1.0))

    def predict_batch(self, X: np.ndarray) -> np.ndarray:
        h = _relu(X @ self.w1 + self.b1)
        out = (h @ self.w2 + self.b2).reshape(-1)
        return np.clip(out, 0.0, 1.0)

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        *,
        epochs: int = 80,
        lr: float = 0.01,
        batch_size: int = 64,
    ) -> Dict[str, float]:
        n = X.shape[0]
        losses: List[float] = []
        for epoch in range(epochs):
            idx = np.random.permutation(n)
            epoch_loss = 0.0
            for start in range(0, n, batch_size):
                batch = idx[start : start + batch_size]
                xb = X[batch]
                yb = y[batch]
                h = _relu(xb @ self.w1 + self.b1)
                pred = (h @ self.w2 + self.b2).reshape(-1)
                err = pred - yb
                epoch_loss += float(np.mean(err ** 2))
                grad_out = (2.0 / len(batch)) * err
                self.w2 -= lr * (h.T @ grad_out).reshape(HIDDEN, 1)
                self.b2 -= lr * grad_out.sum()
                grad_h = grad_out.reshape(-1, 1) @ self.w2.T
                grad_h[h <= 0] = 0
                self.w1 -= lr * (xb.T @ grad_h)
                self.b1 -= lr * grad_h.sum(axis=0)
            losses.append(epoch_loss / max(1, (n + batch_size - 1) // batch_size))
        final = self.predict_batch(X)
        mae = float(np.mean(np.abs(final - y)))
        return {"epochs": epochs, "final_mse": losses[-1] if losses else 0.0, "train_mae": mae}

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        np.savez(path, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2, input_dim=INPUT_DIM, hidden=HIDDEN)

    @classmethod
    def load(cls, path: str) -> "NumpyValueNet":
        data = np.load(path)
        net = cls()
        net.w1 = data["w1"]
        net.b1 = data["b1"]
        net.w2 = data["w2"]
        net.b2 = data["b2"]
        saved_dim = int(data["input_dim"]) if "input_dim" in data else INPUT_DIM
        if saved_dim != INPUT_DIM:
            # Pad or truncate first-layer weights for older checkpoints
            if saved_dim < INPUT_DIM:
                pad = INPUT_DIM - saved_dim
                net.w1 = np.pad(net.w1, ((0, pad), (0, 0)))
            else:
                net.w1 = net.w1[:INPUT_DIM, :]
        return net


if TORCH_AVAILABLE:

    class TorchValueNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(INPUT_DIM, HIDDEN),
                nn.ReLU(),
                nn.Linear(HIDDEN, 1),
                nn.Sigmoid(),
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.net(x).squeeze(-1)


def _sample_training_hands(
    n: int, seed: int = 42,
) -> List[Tuple[str, str, float, float, float, float, float, float]]:
    """Generate training spots: hero, board, pot_odds, position, street, ante, dead, stack_bb."""
    rng = random.Random(seed)
    spots: List[Tuple[str, str, float, float, float, float, float, float]] = []
    deck = list(equity_engine.FULL_DECK)
    depth_choices = [5.0, 10.0, 25.0, 35.0, 50.0, 75.0, 100.0]
    for _ in range(n):
        rng.shuffle(deck)
        hero = equity_engine.cards_str(deck[:2])
        n_board = rng.choice([0, 3, 4, 5])
        board = equity_engine.cards_str(deck[2 : 2 + n_board]) if n_board else ""
        pot_odds = rng.uniform(0.15, 0.55)
        position = rng.uniform(0.0, 1.0)
        street = {0: 0.0, 3: 0.5, 4: 0.75, 5: 1.0}[n_board]
        num_players = rng.choice([0, 6, 9])
        ante = rng.uniform(0.0, 800.0) if num_players else 0.0
        dead = ante * num_players
        stack_bb = rng.choice(depth_choices)
        spots.append((hero, board, pot_odds, position, street, ante, dead, stack_bb))
    return spots


def generate_mc_dataset(
    n_samples: int = 400,
    *,
    iters: int = 3000,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray]:
    """Build (X, y) from Monte Carlo equity vs random opponent."""
    spots = _sample_training_hands(n_samples, seed=seed)
    X = np.zeros((n_samples, INPUT_DIM), dtype=np.float32)
    y = np.zeros(n_samples, dtype=np.float32)
    for i, (hero, board, pot_odds, position, street, ante, dead, stack_bb) in enumerate(spots):
        X[i] = encode_spot(
            hero,
            board,
            pot_odds=pot_odds,
            position=position,
            street=street,
            ante_per_player=ante,
            dead_money=dead,
            stack_bb=stack_bb,
        )
        try:
            res = equity_engine.monte_carlo(
                [hero, _WIDE_RANGE], board=board or None, iters=iters,
            )
            y[i] = res["hero_equity"] / 100.0
        except Exception:
            y[i] = 0.5
    return X, y


def train_value_net(
    *,
    n_samples: int = 400,
    epochs: int = 80,
    model_path: str = DEFAULT_MODEL_PATH,
    seed: int = 42,
    use_torch: bool = False,
) -> Dict[str, Any]:
    """Train value net on MC equity samples and persist weights."""
    X, y = generate_mc_dataset(n_samples=n_samples, seed=seed)
    split = int(n_samples * 0.8)
    X_train, y_train = X[:split], y[:split]
    X_val, y_val = X[split:], y[split:]

    if use_torch and TORCH_AVAILABLE:
        model = TorchValueNet()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.005)
        loss_fn = nn.MSELoss()
        xt = torch.tensor(X_train)
        yt = torch.tensor(y_train)
        for _ in range(epochs):
            model.train()
            optimizer.zero_grad()
            pred = model(xt)
            loss = loss_fn(pred, yt)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_val)).numpy()
        mae = float(np.mean(np.abs(val_pred - y_val))) if len(y_val) else 0.0
        os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
        torch_path = model_path.replace(".npz", ".pt")
        torch.save(model.state_dict(), torch_path)
        meta = {"backend": "torch", "path": torch_path, "val_mae": round(mae, 4), "n_samples": n_samples}
    else:
        net = NumpyValueNet(seed=seed)
        stats = net.train(X_train, y_train, epochs=epochs)
        net.save(model_path)
        val_pred = net.predict_batch(X_val) if len(X_val) else np.array([])
        mae = float(np.mean(np.abs(val_pred - y_val))) if len(y_val) else 0.0
        meta = {
            "backend": "numpy",
            "path": model_path,
            "val_mae": round(mae, 4),
            "n_samples": n_samples,
            **stats,
        }

    meta_path = model_path + ".meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


def _load_net(model_path: str = DEFAULT_MODEL_PATH) -> Optional[NumpyValueNet]:
    if os.path.isfile(model_path):
        try:
            return NumpyValueNet.load(model_path)
        except Exception:
            return None
    return None


def predict_value(
    hero: str,
    board: str = "",
    *,
    pot_odds: float = 0.33,
    position: float = 0.5,
    street: Optional[float] = None,
    ante_per_player: float = 0.0,
    dead_money: float = 0.0,
    bb: float = 1000.0,
    stack_bb: float = 25.0,
    model_path: str = DEFAULT_MODEL_PATH,
) -> Dict[str, Any]:
    """
    Predict equity/EV fraction [0,1] for a spot.

    Falls back to a quick Monte Carlo estimate if no trained weights exist.
    """
    board_cards = equity_engine.parse_cards(board or "")
    if street is None:
        street = {0: 0.0, 3: 0.5, 4: 0.75, 5: 1.0}.get(len(board_cards), 0.0)

    net = _load_net(model_path)
    features = encode_spot(
        hero,
        board,
        pot_odds=pot_odds,
        position=position,
        street=street,
        ante_per_player=ante_per_player,
        dead_money=dead_money,
        bb=bb,
        stack_bb=stack_bb,
    )

    if net is not None:
        value = net.predict(features)
        return {
            "hero": hero,
            "board": board,
            "value_pct": round(value * 100, 2),
            "source": "value_net",
            "model_path": model_path,
            "ante_per_player": ante_per_player,
            "dead_money": dead_money,
            "stack_bb": stack_bb,
            "note": "Neural value estimate — educational approximation, not solver output.",
        }

    # Untrained fallback: quick MC
    try:
        res = equity_engine.monte_carlo([hero, _WIDE_RANGE], board=board or None, iters=2000)
        eq = res["hero_equity"]
    except Exception:
        eq = 50.0
    return {
        "hero": hero,
        "board": board,
        "value_pct": round(eq, 2),
        "source": "monte_carlo_fallback",
        "model_path": None,
        "note": "No trained value_net weights — run POST /api/theory/value/train first.",
    }


def theory_context_block(
    hero: str,
    board: str = "",
    pot_odds: float = 0.33,
    *,
    ante_per_player: float = 0.0,
    dead_money: float = 0.0,
    stack_bb: float = 25.0,
    position: str = "",
) -> str:
    """Short text block for AI coach injection."""
    pos_idx = 0.5
    if position:
        try:
            from theory.charts import CHART_POSITIONS

            pos_idx = CHART_POSITIONS.index(position.upper()) / max(1, len(CHART_POSITIONS) - 1)
        except (ImportError, ValueError):
            pass
    pred = predict_value(
        hero,
        board,
        pot_odds=pot_odds,
        position=pos_idx,
        ante_per_player=ante_per_player,
        dead_money=dead_money,
        stack_bb=stack_bb,
    )
    ante_note = ""
    if ante_per_player > 0:
        ante_note = f" MTT antes: {ante_per_player:.0f}/player, dead money {dead_money:.0f} in pot."
    depth_note = f" Stack≈{stack_bb:.0f}BB."
    return (
        f"NN value estimate @ {stack_bb:.0f}BB: {pred['value_pct']}% equity/EV ({pred['source']})."
        f"{depth_note}{ante_note} {pred.get('note', '')}"
    )
