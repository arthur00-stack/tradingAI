"""Broker contract. Implementations MUST serialize risk check and submission.

Live adapters must reconcile balances, executions, open orders and transfers;
reserve pending buys; deduplicate client IDs; resolve ambiguous submissions;
apply lot/tick/min-notional rules; expose settled withdrawable cash; bound I/O.
An accepted order is not a fill. This synchronous paper contract is a foundation,
not a drop-in implementation of asynchronous exchange execution state machines.
"""

from collections.abc import Callable
from typing import Protocol

from autotrader.core.types import Account, Command, Order, Tick

Guard = Callable[[Account, Order], None]


class Broker(Protocol):
    def snapshot(self) -> Account: ...
    def mark(self, tick: Tick) -> None: ...
    def execute(self, order: Order, guard: Guard) -> Account: ...
    def cancel_all(self) -> None: ...
    def completed(self, command: Command) -> bool: ...
    def transfer(self, command: Command) -> Account: ...
