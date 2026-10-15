"""User constraints and disabled-by-construction connection settings.

Requested leverage is a preference, not an account permission or executable
allocation. Forex spot/CFD eligibility must be established with the broker.
"""

from pathlib import Path
from typing import Literal

from pydantic import model_validator

from autotrader.core.types import D, Positive, Record


class TradingProfile(Record):
    broker: Literal["IBKR"] = "IBKR"
    residence: Literal["FR"] = "FR"
    account_exists: Literal[False] = False
    capital_eur: Positive = D("10")
    max_total_loss_eur: Positive = D("1")
    requested_leverage: Literal[30] = 30
    short_allowed: Literal[False] = False
    live_enabled: Literal[False] = False
    transfers_enabled: Literal[False] = False
    api_host: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    api_read_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_budget(self) -> "TradingProfile":
        if self.max_total_loss_eur > self.capital_eur:
            raise ValueError("Loss allowance cannot exceed the allocated capital")
        return self

    @classmethod
    def load(cls, path: Path) -> "TradingProfile":
        return cls.model_validate_json(path.read_text())
