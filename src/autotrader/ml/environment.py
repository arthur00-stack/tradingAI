"""Single-asset spot environment with causal features and delayed execution."""

from collections import deque
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces
from numpy.typing import NDArray


def features(price: float, previous: float, quantity: float, cash: float) -> NDArray[np.float32]:
    equity = cash + quantity * price
    return np.asarray(
        [np.log(price / previous), quantity * price / max(equity, 1e-9), cash / max(equity, 1e-9)],
        dtype=np.float32,
    )


class MarketEnv(gym.Env[NDArray[np.float32], int]):
    """Actions: hold, target 9% long, close. Fees are charged once in net PnL.

    Decisions at t fill at t+latency, never at the known decision price.
    Reward = log(net_equity_new/net_equity_old) - volatility_weight * trailing_variance.
    Slippage and fees lower net equity, so reward already penalizes them.
    """

    metadata = {"render_modes": []}  # noqa: RUF012 — Gymnasium instance-compatible class contract

    def __init__(
        self,
        prices: NDArray[np.float64],
        latency: int = 1,
        fee: float = 0.001,
        slippage: float = 0.0005,
        volatility_weight: float = 10.0,
    ) -> None:
        super().__init__()
        if prices.ndim != 1 or len(prices) < 4 or not np.all(np.isfinite(prices)) or np.any(prices <= 0):
            raise ValueError("At least four finite positive prices required")
        if latency < 1 or not 0 <= fee < 0.1 or not 0 <= slippage < 0.1 or volatility_weight < 0:
            raise ValueError("Invalid market configuration")
        self.prices, self.latency = prices.copy(), latency
        self.fee, self.slippage, self.volatility_weight = fee, slippage, volatility_weight
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(-np.inf, np.inf, shape=(3,), dtype=np.float32)
        self.t, self.cash, self.quantity = 1, 10_000.0, 0.0
        self.pending: deque[tuple[int, int]] = deque()
        self.returns: deque[float] = deque(maxlen=30)
        self.done = False

    def _observation(self) -> NDArray[np.float32]:
        return features(float(self.prices[self.t]), float(self.prices[self.t - 1]), self.quantity, self.cash)

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        super().reset(seed=seed)
        self.t, self.cash, self.quantity, self.done = 1, 10_000.0, 0.0, False
        self.pending.clear()
        self.returns.clear()
        return self._observation(), {}

    def step(self, action: int) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        if self.done:
            raise RuntimeError("reset() required after end of episode")
        if not self.action_space.contains(action):
            raise ValueError("Invalid action")
        before = self.cash + self.quantity * self.prices[self.t]
        self.pending.append((self.t + self.latency, action))
        self.t += 1
        price = float(self.prices[self.t])
        fees = 0.0
        while self.pending and self.pending[0][0] <= self.t:
            _, due_action = self.pending.popleft()
            equity = self.cash + self.quantity * price
            target = self.quantity if due_action == 0 else (0.09 * equity / price if due_action == 1 else 0.0)
            delta = target - self.quantity
            fill = price * (1 + self.slippage if delta > 0 else 1 - self.slippage)
            cost = abs(delta) * fill * self.fee
            self.cash -= delta * fill + cost
            self.quantity = target
            fees += cost
        equity = self.cash + self.quantity * price
        ret = float(np.log(max(equity, 1e-9) / max(before, 1e-9)))
        self.returns.append(ret)
        reward = ret - self.volatility_weight * float(np.var(self.returns))
        terminated, truncated = equity <= 0, self.t == len(self.prices) - 1
        self.done = terminated or truncated
        return self._observation(), reward, terminated, truncated, {"equity": equity, "fees": fees}
