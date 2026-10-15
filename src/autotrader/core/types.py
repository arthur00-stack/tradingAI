"""Validated boundaries. Monetary arithmetic uses Decimal, never binary floats."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

D = Decimal
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Tick(Record):
    symbol: str
    price: Positive
    timestamp: float = Field(allow_inf_nan=False)


class Order(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    symbol: str
    side: Literal["buy", "sell"]
    quantity: Positive


class Position(Record):
    quantity: Nonnegative = D(0)
    average: Nonnegative = D(0)


class Account(Record):
    cash: Nonnegative = D(0)
    net_deposits: Decimal = Field(default=D(0), allow_inf_nan=False)
    positions: dict[str, Position] = Field(default_factory=dict)
    prices: dict[str, Positive] = Field(default_factory=dict)
    open_orders: list[Order] = Field(default_factory=list)
    revision: int = 0

    @property
    def exposure(self) -> Decimal:
        return sum((p.quantity * self.prices[s] for s, p in self.positions.items()), D(0))

    @property
    def equity(self) -> Decimal:
        return self.cash + self.exposure


class Command(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["deposit", "withdraw"]
    amount: Positive


class ResumeCommand(Record):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["resume"] = "resume"
    panic_generation: int = Field(default=0, ge=0)
