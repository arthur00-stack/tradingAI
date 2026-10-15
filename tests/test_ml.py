from pathlib import Path

import numpy as np
import pytest
import torch
from gymnasium.utils.env_checker import check_env

from autotrader.ml.environment import MarketEnv
from autotrader.ml.model import ActorCritic, load_policy
from autotrader.ml.train import train


def test_gym_contract():
    check_env(MarketEnv(np.linspace(100, 110, 20)), skip_render_check=True)


def test_delay_and_cost_once():
    env = MarketEnv(np.full(8, 100.0), latency=2)
    env.reset()
    _, reward, _, _, info = env.step(1)
    assert env.quantity == 0 and reward == 0
    _, reward, _, _, info = env.step(0)
    assert env.quantity > 0 and reward < 0 and info["fees"] > 0


def test_end_of_data_and_no_future_observation():
    prices = np.asarray([100.0, 100.0, 120.0, 90.0])
    a, b = MarketEnv(prices), MarketEnv(np.asarray([100.0, 100.0, 10.0, 200.0]))
    assert np.array_equal(a.reset()[0], b.reset()[0])
    a.step(0)
    assert a.step(0)[3]
    with pytest.raises(RuntimeError):
        a.step(0)


def test_recurrent_shapes_and_training_checkpoint(tmp_path: Path):
    model = ActorCritic()
    logits, values, hidden = model(torch.zeros(2, 5, 3))
    assert logits.shape == (2, 5, 3) and values.shape == (2, 5)
    assert hidden[0].shape == (1, 2, 64)
    path = tmp_path / "policy.pt"
    losses = train(np.linspace(100, 101, 12), path, episodes=2)
    assert np.all(np.isfinite(losses))
    assert isinstance(load_policy(path), ActorCritic)


def test_failed_checkpoint_write_preserves_previous_model(tmp_path: Path, monkeypatch):
    path = tmp_path / "policy.pt"
    path.write_bytes(b"previous-checkpoint")

    def failed_save(payload, stream):
        stream.write(b"incomplete")
        raise OSError("disk error")

    monkeypatch.setattr(torch, "save", failed_save)
    with pytest.raises(OSError):
        train(np.linspace(100, 101, 5), path, episodes=1)
    assert path.read_bytes() == b"previous-checkpoint"
    assert not list(tmp_path.glob(".policy-*"))


def test_training_multiple_bounded_rollouts(tmp_path: Path, monkeypatch):
    updates = []
    original = torch.optim.Adam.step

    def step(self, *args, **kwargs):
        updates.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(torch.optim.Adam, "step", step)
    path = tmp_path / "bounded.pt"
    losses = train(np.linspace(100, 101, 25), path, episodes=1, rollout_steps=8)
    assert len(updates) == 3  # 23 transitions, with graphs bounded to 8 steps.
    assert np.isfinite(losses).all()
    checkpoint = torch.load(path, weights_only=True)
    assert checkpoint["training"]["rollout_steps"] == 8
    assert checkpoint["training"]["validated"] is False
    assert isinstance(load_policy(path), ActorCritic)
