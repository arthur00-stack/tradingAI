"""Durable execution state machine for future broker adapters.

This module never sends an order. Persist SUBMITTING before network I/O;
a timeout becomes UNKNOWN and MUST be reconciled, never resubmitted blindly.
All mutations use BEGIN IMMEDIATE, including across distinct ledger instances.
Reservations are caller-supplied worst-case cash amounts, not risk approval.
"""

from decimal import Decimal
from typing import Literal

from autotrader.core.store import Store
from autotrader.core.types import D, Nonnegative, Order, Positive, Record

type Status = Literal[
    "INTENT", "SUBMITTING", "UNKNOWN", "OPEN", "PARTIAL", "CANCEL_PENDING", "CANCELED", "REJECTED", "FILLED"
]
TERMINAL = {"CANCELED", "REJECTED", "FILLED"}
TRANSITIONS: dict[str, set[str]] = {
    "INTENT": {"SUBMITTING", "REJECTED"},
    "SUBMITTING": {"UNKNOWN", "OPEN", "PARTIAL", "CANCEL_PENDING", "REJECTED", "CANCELED", "FILLED"},
    "UNKNOWN": {"OPEN", "PARTIAL", "CANCEL_PENDING", "REJECTED", "CANCELED", "FILLED"},
    "OPEN": {"UNKNOWN", "PARTIAL", "CANCEL_PENDING", "CANCELED", "FILLED"},
    "PARTIAL": {"UNKNOWN", "CANCEL_PENDING", "CANCELED", "FILLED"},
    "CANCEL_PENDING": {"UNKNOWN", "PARTIAL", "CANCELED", "FILLED"},
    "CANCELED": {"FILLED"},  # A delayed execution can complete an already canceled order.
    "REJECTED": set(),
    "FILLED": set(),
}


class Execution(Record):
    id: str
    client_id: str
    quantity: Positive
    price: Positive
    fee: Nonnegative


class Entry(Record):
    order: Order
    status: Status = "INTENT"
    reserved_cash: Nonnegative
    filled: Nonnegative = D(0)


class OrderLedger:
    def __init__(self, store: Store) -> None:
        self.store = store
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS live_orders (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def create(self, order: Order, reserve: Decimal, available_cash: Decimal) -> Entry:
        """Atomically reserve cash and record intent; repeat IDs must match exactly."""
        candidate = Entry(order=order, reserved_cash=reserve)
        if not available_cash.is_finite() or available_cash < 0:
            raise ValueError("Invalid available cash")
        if order.side == "buy" and reserve <= 0:
            raise ValueError("Buy requires a worst-case cash reservation")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM live_orders WHERE id=?", (order.id,)).fetchone()
            if row:
                entry = Entry.model_validate_json(row[0])
                if entry.order != order or entry.reserved_cash != reserve:
                    raise ValueError("Client ID collision")
                return entry
            entries = [
                Entry.model_validate_json(row[0]) for row in db.execute("SELECT payload FROM live_orders")
            ]
            used = sum((e.reserved_cash for e in entries if e.status not in TERMINAL), D(0))
            if used + reserve > available_cash:
                raise ValueError("Insufficient unreserved cash")
            db.execute("INSERT INTO live_orders VALUES (?,?)", (order.id, candidate.model_dump_json()))
            db.execute(
                "INSERT INTO journal(kind,payload) VALUES ('order_intent',?)", (candidate.model_dump_json(),)
            )
        return candidate

    def get(self, identifier: str) -> Entry:
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM live_orders WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise KeyError(identifier)
        return Entry.model_validate_json(row[0])

    def transition(self, identifier: str, status: Status) -> Entry:
        """FILLED requires execution evidence; cancel requested is not canceled."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM live_orders WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            current = Entry.model_validate_json(row[0])
            if status == current.status:
                return current
            if status not in TRANSITIONS[current.status]:
                raise ValueError(f"Invalid order transition {current.status} -> {status}")
            if status == "FILLED" and current.filled != current.order.quantity:
                raise ValueError("Missing execution evidence")
            if status == "PARTIAL" and not D(0) < current.filled < current.order.quantity:
                raise ValueError("Missing partial execution evidence")
            if status == "REJECTED" and current.filled:
                raise ValueError("Executed order cannot be rejected")
            updated = current.model_copy(update={"status": status})
            db.execute("UPDATE live_orders SET payload=? WHERE id=?", (updated.model_dump_json(), identifier))
            db.execute(
                "INSERT INTO journal(kind,payload) VALUES ('order_status',?)", (updated.model_dump_json(),)
            )
        return updated

    def fill(self, execution: Execution) -> Entry:
        """Deduplicate fills and preserve fills arriving after cancellation.

        Fees here are in account currency. Corrections/busts and foreign-currency
        fee conversion require explicit adapter events, never mutation of an ID.
        """
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM live_orders WHERE id=?", (execution.client_id,)).fetchone()
            if not row:
                raise KeyError(execution.client_id)
            entry = Entry.model_validate_json(row[0])
            existing = db.execute("SELECT payload FROM executions WHERE id=?", (execution.id,)).fetchone()
            if existing:
                if Execution.model_validate_json(existing[0]) != execution:
                    raise ValueError("Execution ID collision; reconciliation required")
                return entry
            if entry.status in {"INTENT", "REJECTED", "FILLED"}:
                raise ValueError("Unexpected execution; reconciliation required")
            filled = entry.filled + execution.quantity
            if filled > entry.order.quantity:
                raise ValueError("Overfill; reconciliation required")
            status: Status = (
                "FILLED"
                if filled == entry.order.quantity
                else (entry.status if entry.status in {"CANCEL_PENDING", "CANCELED"} else "PARTIAL")
            )
            updated = entry.model_copy(update={"filled": filled, "status": status})
            db.execute("INSERT INTO executions VALUES (?,?)", (execution.id, execution.model_dump_json()))
            db.execute(
                "UPDATE live_orders SET payload=? WHERE id=?",
                (updated.model_dump_json(), execution.client_id),
            )
            db.execute(
                "INSERT INTO journal(kind,payload) VALUES ('execution',?)", (execution.model_dump_json(),)
            )
        return updated

    def unresolved(self) -> list[Entry]:
        """Recovery must resolve all nonterminal entries with the broker."""
        with self.store.connect() as db:
            entries = [
                Entry.model_validate_json(row[0]) for row in db.execute("SELECT payload FROM live_orders")
            ]
        return [entry for entry in entries if entry.status not in TERMINAL]
