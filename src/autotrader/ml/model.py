"""Recurrent actor-critic; same features and action schema in training and inference."""

from pathlib import Path

import torch
from torch import Tensor, nn

type Hidden = tuple[Tensor, Tensor]
SCHEMA = "log-return/exposure/cash:hold/target9/close:v1"


class ActorCritic(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.lstm = nn.LSTM(3, 64, batch_first=True)
        self.actor = nn.Linear(64, 3)
        self.critic = nn.Linear(64, 1)

    def forward(self, x: Tensor, hidden: Hidden | None = None) -> tuple[Tensor, Tensor, Hidden]:
        """x: [batch, time, 3]; hidden: two tensors [1, batch, 64]."""
        output, hidden = self.lstm(x, hidden)
        return self.actor(output), self.critic(output).squeeze(-1), hidden


def load_policy(path: Path) -> ActorCritic:
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint["schema"] != SCHEMA:
        raise ValueError("Incompatible policy/feature schema")
    model = ActorCritic()
    model.load_state_dict(checkpoint["weights"])
    model.eval()
    return model
