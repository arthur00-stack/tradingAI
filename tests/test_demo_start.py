import queue
from pathlib import Path
from threading import Event

from autotrader.broker.paper import PaperBroker
from autotrader.core.engine import Engine
from autotrader.core.feed import Mailbox
from autotrader.core.risk import RiskManager
from autotrader.core.store import Store
from autotrader.core.types import Command, D


def test_demo_funds_only_once_and_never_refills(tmp_path: Path):
    store = Store(tmp_path / "demo.sqlite3")
    broker = PaperBroker(store)
    panic = Event()
    risk = RiskManager(store, panic)
    engine = Engine(
        broker,
        store,
        risk,
        Mailbox(),
        Event(),
        panic,
        queue.Queue(),
        queue.Queue(),
        lambda _: None,
        capital_limit_eur=D(10),
        demo_initial_capital=D(10),
    )
    engine.recover()
    engine.initialize_demo()
    engine.initialize_demo()
    assert broker.snapshot().cash == 10
    assert broker.snapshot().revision == 1
    broker.transfer(Command(kind="withdraw", amount=D(10)))
    engine.initialize_demo()
    assert broker.snapshot().cash == 0
    assert broker.snapshot().revision == 2
