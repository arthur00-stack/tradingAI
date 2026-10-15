"""Deterministic fills, atomic balance/journal updates and idempotent client IDs."""

import json
from datetime import UTC, datetime
from threading import RLock

from autotrader.broker.base import Guard
from autotrader.core.store import Store
from autotrader.core.types import Account, Command, D, Order, Position, Tick

FEE = D("0.001")
SLIPPAGE = D("0.0005")


class PaperBroker:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = RLock()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, request TEXT NOT NULL)")
        saved = store.get("broker")
        self.account = Account.model_validate_json(saved) if saved else Account()

    def snapshot(self) -> Account:
        with self.lock:
            return self.account.model_copy(deep=True)

    def mark(self, tick: Tick) -> None:
        with self.lock:
            updated = self.account.model_copy(
                update={"prices": {**self.account.prices, tick.symbol: tick.price}}
            )
            self.store.put("broker", updated.model_dump_json())
            self.account = updated

    def _commit(
        self, identifier: str, request: str, account: Account, fill: dict[str, str] | None = None
    ) -> Account:
        with self.store.connect() as db:
            db.execute("INSERT INTO operations VALUES (?,?)", (identifier, request))
            db.execute(
                "INSERT INTO state VALUES ('broker',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (account.model_dump_json(),),
            )
            db.execute("INSERT INTO journal(kind,payload) VALUES ('broker',?)", (request,))
            if fill is not None:
                db.execute("INSERT INTO paper_fills(payload) VALUES (?)", (json.dumps(fill),))
        self.account = account
        return self.snapshot()

    def _duplicate(self, identifier: str, request: str) -> bool:
        with self.store.connect() as db:
            row = db.execute("SELECT request FROM operations WHERE id=?", (identifier,)).fetchone()
        if row and row[0] != request:
            raise ValueError("Client ID reused with different payload")
        return row is not None

    def execute(self, order: Order, guard: Guard) -> Account:
        with self.lock:
            request = order.model_dump_json()
            if self._duplicate(order.id, request):
                return self.snapshot()
            guard(self.snapshot(), order)
            a = self.account
            mark = a.prices[order.symbol]
            price = mark * (1 + SLIPPAGE if order.side == "buy" else 1 - SLIPPAGE)
            notional = order.quantity * price
            fee = notional * FEE
            position = a.positions.get(order.symbol, Position())
            if order.side == "buy":
                cash = a.cash - notional - fee
                quantity = position.quantity + order.quantity
                average = (position.quantity * position.average + notional + fee) / quantity
            else:
                if order.quantity > position.quantity:
                    raise ValueError("Short selling forbidden")
                cash = a.cash + notional - fee
                quantity = position.quantity - order.quantity
                average = position.average if quantity else D(0)
            if cash < 0:
                raise ValueError("Insufficient cash")
            positions = {**a.positions, order.symbol: Position(quantity=quantity, average=average)}
            if not quantity:
                del positions[order.symbol]
            updated = Account(
                cash=cash,
                net_deposits=a.net_deposits,
                positions=positions,
                prices=a.prices,
                revision=a.revision + 1,
            )
            return self._commit(
                order.id,
                request,
                updated,
                {
                    "order_id": order.id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": str(order.quantity),
                    "price": str(price),
                    "fee": str(fee),
                },
            )

    def cancel_all(self) -> None:
        # Paper fills are immediate: no outstanding orders.
        with self.lock:
            self.store.log("cancel_all", "paper: no open orders")

    def completed(self, command: Command) -> bool:
        with self.lock:
            return self._duplicate(command.id, command.model_dump_json())

    def transfer(self, command: Command) -> Account:
        with self.lock:
            request = command.model_dump_json()
            if self._duplicate(command.id, request):
                return self.snapshot()
            delta = command.amount if command.kind == "deposit" else -command.amount
            cash = self.account.cash + delta
            if cash < 0:
                raise ValueError("Insufficient settled cash")
            account = self.account.model_copy(
                update={
                    "cash": cash,
                    "net_deposits": self.account.net_deposits + delta,
                    "revision": self.account.revision + 1,
                }
            )
            return self._commit(command.id, request, account)
