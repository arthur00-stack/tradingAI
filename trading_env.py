import gymnasium as gym
from gymnasium import spaces
import numpy as np


class StockTradingEnv(gym.Env):
    """Environnement de simulation de trading pour l'agent IA."""

    def __init__(
        self, df, initial_balance=10000, window_size=10, commission=0.001
    ):
        super(StockTradingEnv, self).__init__()

        self.df = df.reset_index(drop=True)
        self.initial_balance = initial_balance
        self.window_size = window_size
        self.commission = commission

        # 3 Actions : 0 = Hold, 1 = Buy, 2 = Sell
        self.action_space = spaces.Discrete(3)

        # 5 Caractéristiques : Close, Volume, SMA_10, SMA_30, RSI
        self.num_features = 5
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(self.window_size, self.num_features),
            dtype=np.float32,
        )

        self.reset()

    def _get_observation(self):
        """Retourne la séquence des N dernières bougies normalisées."""
        frame = self.df.iloc[
            self.current_step - self.window_size : self.current_step
        ]
        obs = frame[["Close", "Volume", "SMA_10", "SMA_30", "RSI"]].values

        # Normalisation relative
        norm_obs = obs / (obs[-1, 0] + 1e-8)
        return norm_obs.astype(np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)

        self.balance = self.initial_balance
        self.shares_held = 0
        self.net_worth = self.initial_balance
        self.current_step = self.window_size

        return self._get_observation(), {}

    def step(self, action):
        current_price = self.df.iloc[self.current_step]["Close"]

        # Execution des ordres
        if action == 1:  # ACHETER
            possible_shares = int(
                self.balance / (current_price * (1 + self.commission))
            )
            if possible_shares > 0:
                cost = (
                    possible_shares * current_price * (1 + self.commission)
                )
                self.shares_held += possible_shares
                self.balance -= cost

        elif action == 2:  # VENDRE
            if self.shares_held > 0:
                revenue = (
                    self.shares_held * current_price * (1 - self.commission)
                )
                self.balance += revenue
                self.shares_held = 0

        self.current_step += 1
        done = self.current_step >= len(self.df) - 1

        new_net_worth = self.balance + (self.shares_held * current_price)
        reward = (new_net_worth - self.net_worth) / self.net_worth
        self.net_worth = new_net_worth

        obs = self._get_observation()
        info = {"net_worth": self.net_worth, "balance": self.balance}

        return obs, reward, done, False, info