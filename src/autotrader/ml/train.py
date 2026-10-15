"""Minimal on-policy recurrent A2C trainer; no claim of investment performance."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np
import torch
from torch.distributions import Categorical

from autotrader.ml.environment import MarketEnv
from autotrader.ml.model import SCHEMA, ActorCritic


def train(
    prices: np.ndarray, output: Path, episodes: int, seed: int = 7, rollout_steps: int = 128
) -> list[float]:
    """Recurrent A2C with bounded BPTT, entropy regularization and gradient clipping.

    Only pass training data. Hidden state is carried but detached between updates,
    bounding the autograd graph independently of series length. This improves
    memory scaling, not evidence of returns. Hyperparameters require validation
    followed by an untouched final test.
    """
    if episodes <= 0 or rollout_steps < 2:
        raise ValueError("episodes must be positive and rollout_steps >= 2")
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    model = ActorCritic()
    optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
    losses: list[float] = []
    for episode in range(episodes):
        env = MarketEnv(prices)
        obs, _ = env.reset(seed=seed + episode)
        hidden = None
        done = False
        episode_losses: list[float] = []
        while not done:
            log_probs, values, entropies, rewards = [], [], [], []
            terminated = False
            for _ in range(rollout_steps):
                logits, value, hidden = model(torch.from_numpy(obs).reshape(1, 1, 3), hidden)
                distribution = Categorical(logits=logits[0, 0])
                action = distribution.sample()
                obs, reward, terminated, truncated, _ = env.step(int(action.item()))
                log_probs.append(distribution.log_prob(action))
                values.append(value[0, 0])
                entropies.append(distribution.entropy())
                rewards.append(reward)
                done = terminated or truncated
                if done:
                    break
            with torch.no_grad():
                _, last_value, _ = model(torch.from_numpy(obs).reshape(1, 1, 3), hidden)
            discounted = 0.0 if terminated else float(last_value.item())
            returns: list[float] = []
            for reward in reversed(rewards):
                discounted = reward + 0.99 * discounted
                returns.append(discounted)
            target = torch.tensor(list(reversed(returns)), dtype=torch.float32)
            advantage = target - torch.stack(values)
            loss = (
                -(torch.stack(log_probs) * advantage.detach()).mean()
                + 0.5 * advantage.square().mean()
                - 0.01 * torch.stack(entropies).mean()
            )
            optimizer.zero_grad()
            loss.backward()
            nnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            if not torch.isfinite(loss) or not torch.isfinite(nnorm):
                raise RuntimeError("Non-finite training loss/gradient")
            optimizer.step()
            assert hidden is not None
            hidden = (hidden[0].detach(), hidden[1].detach())
            episode_losses.append(float(loss.detach()))
        losses.append(float(np.mean(episode_losses)))
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".policy-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            torch.save(
                {
                    "schema": SCHEMA,
                    "weights": model.state_dict(),
                    "training": {
                        "seed": seed,
                        "episodes": episodes,
                        "rollout_steps": rollout_steps,
                        "prices_sha256": hashlib.sha256(
                            np.asarray(prices, dtype="<f8").tobytes()
                        ).hexdigest(),
                        "observations": len(prices),
                        "validated": False,
                    },
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return losses


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prices", type=Path, help=".npy chronological training-only positive prices")
    parser.add_argument("--output", type=Path, default=Path("var/policy.pt"))
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--rollout-steps", type=int, default=128)
    args = parser.parse_args()
    losses = train(
        np.load(args.prices, allow_pickle=False),
        args.output,
        args.episodes,
        rollout_steps=args.rollout_steps,
    )
    print(f"Checkpoint {args.output}; final loss={losses[-1]:.6f}. Validation out-of-sample required.")


if __name__ == "__main__":
    main()
