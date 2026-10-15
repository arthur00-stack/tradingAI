import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from autotrader.broker.paper import PaperBroker
from autotrader.core.engine import Engine
from autotrader.core.feed import Mailbox
from autotrader.core.risk import Rejected, RiskManager
from autotrader.core.store import Store
from autotrader.core.types import Account, Command, D, Order, Tick


@pytest.fixture
def system(tmp_path: Path):
    store = Store(tmp_path / "test.sqlite3")
    broker = PaperBroker(store)
    panic, stop = Event(), Event()
    risk = RiskManager(store, panic)
    mailbox = Mailbox()
    tick = Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time())
    mailbox.put(tick)
    broker.mark(tick)
    broker.transfer(Command(kind="deposit", amount=D(10000)))
    engine = Engine(broker, store, risk, mailbox, stop, panic, queue.Queue(), queue.Queue(), lambda _: None)
    engine.recover()
    return broker, risk, store, engine


def test_strict_order_validation():
    for quantity in (D(-1), D(0), D("NaN"), 1.0, "1"):
        with pytest.raises(ValidationError):
            Order(symbol="X", side="buy", quantity=quantity)


def test_recovery_barrier(system):
    broker, risk, _, _ = system
    risk.ready = False
    with pytest.raises(Rejected):
        broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(1)), risk.validate)


def test_exposure_is_aggregate_and_sticky(system):
    broker, risk, store, _ = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    with pytest.raises(Rejected):
        broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(2)), risk.validate)
    assert risk.state.suspended
    assert RiskManager(store, Event()).state.suspended
    assert broker.snapshot().positions["DEMO/EUR"].quantity == 9


def test_drawdown_triggers_without_order_and_flow_neutral(system):
    broker, risk, _, _ = system
    a = broker.snapshot()
    assert risk.observe(a) == 0
    broker.transfer(Command(kind="withdraw", amount=D(1000)))
    assert risk.observe(broker.snapshot()) == 0
    risk.observe(Account(cash=D(8550), net_deposits=D(9000)))
    assert risk.state.suspended  # Exactly 5% from 9000.


def test_daily_boundary_and_no_auto_rearm(system):
    _, _, store, _ = system
    day = ["2026-09-30"]
    risk = RiskManager(store, Event(), lambda: day[0])
    risk.observe(Account(cash=D(10000), net_deposits=D(10000)))
    risk.observe(Account(cash=D(9500), net_deposits=D(10000)))
    day[0] = "2026-10-01"
    assert risk.observe(Account(cash=D(9500), net_deposits=D(10000))) == 0
    assert risk.state.suspended


def test_idempotency_crash_recovery(system):
    broker, risk, store, _ = system
    order = Order(symbol="DEMO/EUR", side="buy", quantity=D(1))
    first = broker.execute(order, risk.validate)
    second = PaperBroker(store).execute(order, risk.validate)
    assert first == second
    with pytest.raises(ValueError):
        broker.execute(order.model_copy(update={"quantity": D(2)}), risk.validate)


def test_atomic_concurrent_validation(system):
    broker, risk, _, _ = system

    def buy():
        try:
            broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(6)), risk.validate)
            return True
        except Rejected:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: buy(), range(2))) == [False, True]
    assert broker.snapshot().positions["DEMO/EUR"].quantity == 6


def test_cashout_partial_and_accounting(system):
    broker, risk, store, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    command = Command(kind="withdraw", amount=D(9500))
    engine._command(command)
    a = broker.snapshot()
    assert a.net_deposits == 500
    assert 0 < a.positions["DEMO/EUR"].quantity < 9
    assert a.cash >= 0
    assert a.equity < 500  # Fees and slippage, no fabricated money.
    assert not store.get("cashout_pending")


def test_impossible_cashout_does_not_liquidate(system):
    broker, risk, _, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    before = broker.snapshot()
    with pytest.raises(ValueError):
        engine._command(Command(kind="withdraw", amount=D(10000)))
    assert broker.snapshot() == before


def test_panic_bypasses_suspended_policy_and_flattens(system):
    broker, risk, _, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    engine.panic.set()
    engine.stop.set()
    engine.safety_loop()
    assert not broker.snapshot().positions
    assert risk.state.suspended
    with pytest.raises(Rejected):
        broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(1)), risk.validate)


def test_stale_feed_drops_pending_inference(system):
    _, _, _, engine = system
    engine.mailbox.latest = Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time() - 30)
    engine.inflight = ("old", 1, time.monotonic())
    engine.cycle()
    assert engine.inflight is None
    assert "périmé" in engine.status


def test_recovery_resets_memory_on_broker_divergence(system):
    broker, _, _, engine = system
    engine.memory = [[[1.0]]]
    engine.save()
    broker.transfer(Command(kind="deposit", amount=D(10)))
    engine.recover()
    assert engine.memory is None


def test_cashout_retry_is_idempotent(system):
    broker, risk, _, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    command = Command(kind="withdraw", amount=D(9500))
    engine._command(command)
    before = broker.snapshot()
    engine._command(command)
    assert broker.snapshot() == before


def test_pending_hidden_state_is_not_restored(system):
    _, _, _, engine = system
    engine.memory = [[[1.0]]]
    engine.inflight = ("pending", 0, time.monotonic())
    engine.save()
    engine.recover()
    assert engine.memory is None


@pytest.mark.parametrize("flow", [D(10000), D(-9000)])
def test_flows_do_not_dilute_or_amplify_existing_drawdown(system, flow):
    _, risk, _, _ = system
    assert risk.observe(Account(cash=D(9600), net_deposits=D(10000))) == pytest.approx(0.04)
    assert risk.observe(Account(cash=D(9600) + flow, net_deposits=D(10000) + flow)) == pytest.approx(0.04)
    # A new market loss still compounds on the current invested equity.
    next_equity = (D(9600) + flow) * D(".98")
    assert risk.observe(Account(cash=next_equity, net_deposits=D(10000) + flow)) > 0.05
    assert risk.state.suspended


def test_nav_and_drawdown_survive_restart(system):
    _, risk, store, _ = system
    risk.observe(Account(cash=D(9600), net_deposits=D(10000)))
    risk.observe(Account(cash=D(600), net_deposits=D(1000)))
    risk.save()
    restored = RiskManager(store, Event())
    assert restored.observe(Account(cash=D(600), net_deposits=D(1000))) == pytest.approx(0.04)


def test_legacy_risk_state_is_not_silently_rebased(system):
    _, _, store, _ = system
    store.put("risk", '{"day":"2026-09-30","peak":"10000","flows":"10000"}')
    assert RiskManager(store, Event()).state.suspended


def test_profile_cap_rejects_extra_deposit(system):
    broker, _, _, engine = system
    engine.capital_limit_eur = D(10000)
    before = broker.snapshot()
    with pytest.raises(Rejected):
        engine._command(Command(kind="deposit", amount=D(1)))
    assert broker.snapshot() == before


def test_profile_cap_applies_to_existing_account_on_recovery(system):
    _, risk, _, engine = system
    engine.capital_limit_eur = D(10)
    engine.recover()
    assert risk.state.suspended
    assert "capital existant" in risk.state.reason


def test_ten_euro_demo_can_open_and_close_target(system):
    broker, _, _, engine = system
    broker.transfer(Command(kind="withdraw", amount=D(9990)))
    tick = Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time())
    engine._decision(1, tick)
    assert broker.snapshot().positions["DEMO/EUR"].quantity == D("0.009")
    engine._decision(2, tick)
    assert not broker.snapshot().positions


def test_five_percent_global_stop_across_days(system):
    _, _, store, _ = system
    day = ["2026-09-30"]
    risk = RiskManager(store, Event(), today=lambda: day[0])
    risk.observe(Account(cash=D(10000), net_deposits=D(10000)))
    risk.observe(Account(cash=D(9700), net_deposits=D(10000)))
    day[0] = "2026-10-01"
    risk.observe(Account(cash=D(9600), net_deposits=D(10000)))
    assert not risk.panic.is_set()
    risk.observe(Account(cash=D(9500), net_deposits=D(10000)))
    assert risk.panic.is_set() and risk.state.liquidation_required


def test_exact_half_euro_stop_and_persistent_request(tmp_path):
    store = Store(tmp_path / "stop.sqlite3")
    risk = RiskManager(store, Event())
    risk.observe(Account(cash=D(10), net_deposits=D(10)))
    risk.observe(Account(cash=D("9.5001"), net_deposits=D(10)))
    assert not risk.panic.is_set()
    risk.observe(Account(cash=D("9.5"), net_deposits=D(10)))
    restored = RiskManager(store, Event())
    assert restored.panic.is_set() and restored.state.suspended
    restored.complete_liquidation()
    restarted = RiskManager(store, Event())
    assert restarted.state.suspended and restarted.state.liquidation_completed
    assert not restarted.panic.is_set()


def test_market_loss_causes_liquidation_and_remains_stopped(system):
    broker, risk, store, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    engine.mailbox.put(Tick(symbol="DEMO/EUR", price=D(40), timestamp=time.time()))
    engine.cycle()
    assert engine.panic.is_set()
    engine.stop.set()
    engine.safety_loop()
    assert not broker.snapshot().positions
    assert risk.state.liquidation_completed
    assert "5 %" in risk.state.reason
    restored = RiskManager(store, Event())
    assert restored.state.suspended
    with pytest.raises(Rejected):
        broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(1)), restored.validate)


def test_failed_liquidation_is_retained_for_recovery(system, monkeypatch):
    broker, risk, store, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)

    def unavailable(*args):
        raise RuntimeError("Broker unavailable")

    monkeypatch.setattr(broker, "execute", unavailable)
    engine.panic.set()
    engine.stop.set()
    engine.safety_loop()
    assert risk.state.liquidation_required and not risk.state.liquidation_completed
    assert RiskManager(store, Event()).panic.is_set()


def test_latest_mark_survives_broker_restart_without_trade(system):
    broker, risk, store, _ = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    broker.mark(Tick(symbol="DEMO/EUR", price=D(110), timestamp=time.time()))
    assert PaperBroker(store).snapshot() == broker.snapshot()


def test_completed_withdrawal_reconciles_without_second_transfer(system):
    broker, risk, store, engine = system
    command = Command(kind="withdraw", amount=D(1))
    store.put("cashout_pending", command.model_dump_json())
    broker.transfer(command)
    before = broker.snapshot()
    engine.reconcile_cashout()
    assert not store.get("cashout_pending") and not risk.state.suspended
    assert broker.snapshot() == before


def test_unresolved_cashout_stays_suspended(system):
    _, risk, store, engine = system
    command = Command(kind="withdraw", amount=D(1))
    store.put("cashout_pending", command.model_dump_json())
    engine.reconcile_cashout()
    assert risk.state.suspended and store.get("cashout_pending")


def test_cashout_stops_if_risk_trips_before_transfer(system, monkeypatch):
    broker, risk, store, engine = system
    before = broker.snapshot()
    observe = risk.observe

    def trip(account, **kwargs):
        result = observe(account, **kwargs)
        risk.request_liquidation("Risk detected during cash-out")
        return result

    monkeypatch.setattr(risk, "observe", trip)
    with pytest.raises(Rejected):
        engine._command(Command(kind="withdraw", amount=D(1)))
    assert broker.snapshot().cash == before.cash
    assert not store.get("cashout_pending")


def test_liquidation_failure_remains_visible(system, monkeypatch):
    _, _, _, engine = system
    captured = []
    engine.publish = captured.append
    engine.safety_error = "PANIC ÉCHEC : broker unavailable"
    engine.report()
    assert captured[-1]["status"].startswith("PANIC ÉCHEC")


def test_rearm_and_repeat_panic(system):
    from autotrader.core.types import ResumeCommand

    broker, risk, _, engine = system
    for _ in range(2):
        broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(1)), risk.validate)
        engine.panic.set()
        engine.stop.set()
        engine.safety_loop()
        assert not broker.snapshot().positions
        engine.stop.clear()
        engine._command(ResumeCommand())
        assert not risk.state.suspended and not engine.panic.is_set()
    assert risk.state.session_number == 3


def test_withdraw_after_panic_without_reactivation(system):
    broker, risk, _, engine = system
    engine.panic.set()
    engine.stop.set()
    engine.safety_loop()
    engine.stop.clear()
    engine.mailbox.latest = None
    engine._command(Command(kind="withdraw", amount=D(10000)))
    assert broker.snapshot().cash == 0
    assert risk.state.suspended and engine.panic.is_set()


def test_withdraw_after_policy_failure(system):
    broker, risk, _, engine = system
    risk.suspend("Inference failed")
    engine.inference_fault = True
    engine._command(Command(kind="withdraw", amount=D(1)))
    assert broker.snapshot().cash == 9999
    assert risk.state.suspended


def test_rearm_session_preserves_lifetime_loss(tmp_path):
    from autotrader.core.panic import PanicEvent

    panic = PanicEvent()
    risk = RiskManager(Store(tmp_path / "session.sqlite3"), panic, max_total_loss_eur=D(1))
    risk.observe(Account(cash=D(10), net_deposits=D(10)))
    account = Account(cash=D("9.5"), net_deposits=D(10))
    risk.observe(account)
    risk.complete_liquidation()
    risk.rearm(account, panic.generation)
    assert risk.state.nav == "0.95"
    assert risk.state.session_nav == "0.95"
    risk.observe(Account(cash=D("9.03"), net_deposits=D(10)))
    assert not panic.is_set()
    risk.observe(Account(cash=D("9.025"), net_deposits=D(10)))
    assert panic.is_set()
    risk.complete_liquidation()
    with pytest.raises(Rejected, match="globale"):
        risk.rearm(Account(cash=D(9), net_deposits=D(10)), panic.generation)


def test_new_panic_has_priority_over_queued_rearm(tmp_path):
    from autotrader.core.panic import PanicEvent

    panic = PanicEvent()
    risk = RiskManager(Store(tmp_path / "race.sqlite3"), panic)
    account = Account(cash=D(10), net_deposits=D(10))
    risk.observe(account)
    risk.request_liquidation("PANIC")
    risk.complete_liquidation()
    generation = panic.generation
    panic.set()
    with pytest.raises(Rejected, match="priorité"):
        risk.rearm(account, generation)
    assert panic.is_set() and risk.state.suspended


def test_rearm_active_and_stale_are_rejected(system):
    from autotrader.core.types import ResumeCommand

    _, risk, _, engine = system
    with pytest.raises(Rejected, match="déjà actif"):
        engine._command(ResumeCommand())
    risk.suspend("Stopped")
    engine.mailbox.latest = None
    with pytest.raises(Rejected, match="flux"):
        engine._command(ResumeCommand())
    assert risk.state.suspended


def test_pending_liquidation_blocks_transfer(system):
    broker, risk, _, engine = system
    risk.request_liquidation("PANIC")
    before = broker.snapshot()
    with pytest.raises(Rejected, match="Liquidation"):
        engine._command(Command(kind="withdraw", amount=D(1)))
    assert broker.snapshot() == before


def test_stale_cashout_can_be_retried(system):
    broker, risk, store, engine = system
    broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(9)), risk.validate)
    engine.mailbox.latest = None
    with pytest.raises(Rejected, match="périmés"):
        engine._command(Command(kind="withdraw", amount=D(9500)))
    assert not store.get("cashout_pending")
    engine.mailbox.put(Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time()))
    engine._command(Command(kind="withdraw", amount=D(9500)))
    assert broker.snapshot().net_deposits == 500


def test_same_safety_thread_handles_second_activation(system):
    from threading import Thread

    from autotrader.core.types import ResumeCommand

    broker, risk, _, engine = system
    worker = Thread(target=engine.safety_loop)
    worker.start()
    try:
        for _ in range(2):
            broker.execute(Order(symbol="DEMO/EUR", side="buy", quantity=D(1)), risk.validate)
            engine.panic.set()
            deadline = time.monotonic() + 3
            while not risk.state.liquidation_completed and time.monotonic() < deadline:
                time.sleep(0.01)
            assert risk.state.liquidation_completed
            assert not broker.snapshot().positions
            engine.mailbox.put(Tick(symbol="DEMO/EUR", price=D(100), timestamp=time.time()))
            engine._command(ResumeCommand())
    finally:
        engine.stop.set()
        worker.join(3)
    assert not worker.is_alive()
