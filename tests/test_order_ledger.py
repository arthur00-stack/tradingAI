from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from autotrader.broker.ledger import Execution, OrderLedger
from autotrader.core.store import Store
from autotrader.core.types import D, Order


def setup(tmp_path: Path):
    ledger = OrderLedger(Store(tmp_path / "orders.sqlite3"))
    order = Order(symbol="X/EUR", side="buy", quantity=D(10))
    ledger.create(order, D(1010), D(2000))
    return ledger, order


def test_unknown_submission_remains_reserved_after_restart(tmp_path):
    ledger, order = setup(tmp_path)
    ledger.transition(order.id, "SUBMITTING")
    ledger.transition(order.id, "UNKNOWN")
    restored = OrderLedger(Store(tmp_path / "orders.sqlite3"))
    assert restored.unresolved()[0].status == "UNKNOWN"
    with pytest.raises(ValueError):
        restored.create(Order(symbol="Y/EUR", side="buy", quantity=D(10)), D(1010), D(2000))
    with pytest.raises(ValueError):
        restored.transition(order.id, "SUBMITTING")  # No automatic retry after timeout.


def test_partial_duplicate_and_late_execution(tmp_path):
    ledger, order = setup(tmp_path)
    ledger.transition(order.id, "SUBMITTING")
    ledger.transition(order.id, "OPEN")
    fill = Execution(id="fill-1", client_id=order.id, quantity=D(4), price=D(100), fee=D(".4"))
    assert ledger.fill(fill).status == "PARTIAL"
    assert ledger.fill(fill).filled == 4
    ledger.transition(order.id, "CANCEL_PENDING")
    ledger.transition(order.id, "CANCELED")
    late = Execution(id="fill-2", client_id=order.id, quantity=D(2), price=D(100), fee=D(".2"))
    state = ledger.fill(late)
    assert state.status == "CANCELED" and state.filled == 6
    with pytest.raises(ValueError):
        ledger.fill(late.model_copy(update={"quantity": D(3)}))


def test_fill_evidence_and_overfill(tmp_path):
    ledger, order = setup(tmp_path)
    ledger.transition(order.id, "SUBMITTING")
    with pytest.raises(ValueError):
        ledger.transition(order.id, "FILLED")
    with pytest.raises(ValueError):
        ledger.fill(Execution(id="over", client_id=order.id, quantity=D(11), price=D(100), fee=D(1)))
    assert ledger.get(order.id).filled == 0
    entry = ledger.fill(Execution(id="ok", client_id=order.id, quantity=D(10), price=D(100), fee=D(1)))
    assert entry.status == "FILLED"


def test_cross_connection_reservation_is_atomic(tmp_path):
    store = Store(tmp_path / "orders.sqlite3")
    OrderLedger(store)

    def reserve(_):
        ledger = OrderLedger(store)
        try:
            ledger.create(Order(symbol="X/EUR", side="buy", quantity=D(6)), D(600), D(1000))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]
