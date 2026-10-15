import csv
import json
import time
from pathlib import Path

from autotrader.broker.paper import PaperBroker
from autotrader.core.reporting import export_report
from autotrader.core.store import Store
from autotrader.core.types import Command, D, Order, Tick


def test_report_counts_fees_once_and_preserves_net_pnl(tmp_path: Path):
    store = Store(tmp_path / "paper.sqlite3")
    broker = PaperBroker(store)
    broker.transfer(Command(kind="deposit", amount=D(10)))
    broker.mark(Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time()))
    order = Order(symbol="DEMO/EUR", side="buy", quantity=D(".009"))
    broker.execute(order, lambda *_: None)
    broker.execute(order, lambda *_: None)
    store.record_metric(time.time(), str(broker.snapshot().equity), "1")
    path = export_report(store)
    data = json.loads(path.with_suffix(".json").read_text())
    assert data["trade_count"] == 1 and len(data["fills"]) == 1
    assert D(data["recorded_fees_eur"]) == D(".00090045")
    assert D(data["net_pnl_eur"]) == broker.snapshot().equity - 10
    assert data["complete_fee_history"]
    assert "Simulation uniquement" in path.read_text()
    assert len(list(csv.reader((path.parent / "operations.csv").open()))) == 2
    assert path.stat().st_mode & 0o777 == 0o600


def test_legacy_report_does_not_claim_complete_fee_history(tmp_path: Path):
    store = Store(tmp_path / "paper.sqlite3")
    broker = PaperBroker(store)
    broker.transfer(Command(kind="deposit", amount=D(10)))
    with store.connect() as db:
        db.execute(
            "INSERT INTO operations VALUES (?,?)",
            ("old", Order(id="old", symbol="X", side="buy", quantity=D(1)).model_dump_json()),
        )
    path = export_report(store)
    assert not json.loads(path.with_suffix(".json").read_text())["complete_fee_history"]


def test_report_escapes_html_and_spreadsheet_formulas(tmp_path: Path):
    store = Store(tmp_path / "paper.sqlite3")
    broker = PaperBroker(store)
    broker.transfer(Command(kind="deposit", amount=D(10)))
    broker.mark(Tick(symbol="=malicious()", price=D(1), timestamp=time.time()))
    broker.execute(Order(symbol="=malicious()", side="buy", quantity=D(1)), lambda *_: None)
    store.put("risk", json.dumps({"reason": "<script>alert(1)</script>"}))
    path = export_report(store)
    assert "<script>" not in path.read_text()
    assert "&lt;script&gt;" in path.read_text()
    assert "'=malicious()" in (path.parent / "operations.csv").read_text()


def test_metric_retention_and_history(tmp_path: Path):
    store = Store(tmp_path / "paper.sqlite3")
    now = time.time()
    store.record_metric(now - 8 * 86400, "1", "1")
    store.record_metric(now, "10", "1")
    store.record_metric(now + 1, "9", ".9")
    assert store.history() == [10.0, 9.0]
    assert store.history(limit=1) == [9.0]
