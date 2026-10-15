"""Recovery barrier, bounded commands, autonomous decisions and safety controller."""

import hashlib
import json
import logging
import queue
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from threading import Event, RLock
from typing import Any

import numpy as np

from autotrader.broker.base import Broker
from autotrader.broker.paper import FEE, SLIPPAGE
from autotrader.core.feed import Mailbox
from autotrader.core.panic import PanicEvent
from autotrader.core.reporting import export_report
from autotrader.core.risk import Rejected, RiskManager
from autotrader.core.store import Store
from autotrader.core.types import Command, D, Order, ResumeCommand, Tick
from autotrader.ml.environment import features

log = logging.getLogger(__name__)


class Engine:
    def __init__(
        self,
        broker: Broker,
        store: Store,
        risk: RiskManager,
        mailbox: Mailbox,
        stop: Event,
        panic: Event,
        requests: Any,
        responses: Any,
        publish: Callable[[dict[str, Any]], None],
        checkpoint: Path | None = None,
        capital_limit_eur: Decimal | None = None,
        demo_initial_capital: Decimal | None = None,
        inference_alive: Callable[[], bool] = lambda: True,
    ) -> None:
        self.broker, self.store, self.risk, self.mailbox = broker, store, risk, mailbox
        self.stop, self.panic, self.requests, self.responses = stop, panic, requests, responses
        self.publish = publish
        self.capital_limit_eur = capital_limit_eur
        self.demo_initial_capital = demo_initial_capital
        self.commands: queue.Queue[Command | ResumeCommand] = queue.Queue(maxsize=32)
        self.inference_alive = inference_alive
        self.inference_fault = False
        self.operations = RLock()
        self.memory: Any = None
        self.previous_price: float | None = None
        self.last_tick = 0.0
        self.last_metric_tick = 0.0
        self.inflight: tuple[str, int, float] | None = None
        self.model_id = hashlib.sha256(checkpoint.read_bytes()).hexdigest() if checkpoint else "hold-only"
        self.status = "RECOVERY"
        self.recovered = Event()
        self.export_requested = Event()
        self.safety_error = ""
        self.last_metric_saved = 0.0
        self.send_history = True
        self.fills_revision = -1
        self.recent_fills: list[dict[str, str]] = []

    def recover(self) -> None:
        """Broker is authoritative; unmatched or old recurrence is invalidated."""
        with self.operations:
            account = self.broker.snapshot()  # Failure must leave ready=False.
            if account.open_orders:
                self.broker.cancel_all()
                account = self.broker.snapshot()
                if account.open_orders:
                    raise RuntimeError("Unconfirmed cancellation during recovery")
            raw = self.store.get("engine")
            saved = json.loads(raw) if raw else None
            compatible = (
                saved
                and saved["account"] == account.model_dump(mode="json")
                and not saved.get("inflight", True)
                and saved["model_id"] == self.model_id
                and 0 <= time.time() - saved["time"] < 5
            )
            self.memory = saved["memory"] if compatible and saved is not None else None
            self.previous_price = saved["previous_price"] if compatible and saved is not None else None
            self.store.log(
                "recovery", json.dumps({"matched": bool(compatible), "revision": account.revision})
            )
            # Broker positions/cash replace stale local copies; no artificial balancing trades.
            self.risk.observe(account)
            if self.capital_limit_eur is not None and account.net_deposits > self.capital_limit_eur:
                self.risk.suspend("Recovery : capital existant supérieur au profil autorisé")
            if account.equity and account.exposure > account.equity * D(".10"):
                self.risk.suspend("Recovery : exposition existante > 10 %")
            self.risk.ready = True
            self.recovered.set()
            self.status = "PAPER / " + ("policy" if self.model_id != "hold-only" else "HOLD — aucun modèle")
            self.save()

    def save(self) -> None:
        with self.operations:
            self.store.put(
                "engine",
                json.dumps(
                    {
                        "account": self.broker.snapshot().model_dump(mode="json"),
                        "model_id": self.model_id,
                        "memory": self.memory,
                        "previous_price": self.previous_price,
                        "time": time.time(),
                        "inflight": self.inflight is not None,
                    }
                ),
            )
            self.risk.save()

    def submit(self, command: Command | ResumeCommand) -> None:
        self.commands.put_nowait(command)

    def submit_resume(self) -> None:
        generation = self.panic.generation if isinstance(self.panic, PanicEvent) else 0
        self.submit(ResumeCommand(panic_generation=generation))

    def resume(self, command: ResumeCommand) -> None:
        with self.operations:
            if self.stop.is_set() or not self.recovered.is_set():
                raise Rejected("Compte non réconcilié ou application en cours de fermeture")
            if self.inference_fault or not self.inference_alive():
                raise Rejected("Inférence indisponible : redémarrer l'application avant de réactiver")
            if self.store.get("cashout_pending"):
                self.reconcile_cashout()
                if self.store.get("cashout_pending"):
                    raise Rejected("Retrait incertain à rapprocher avant réactivation")
            tick = self.mailbox.get()
            if not self._fresh(tick):
                raise Rejected("Attendre le rétablissement du flux avant réactivation")
            assert tick is not None
            self.broker.mark(tick)
            account = self.broker.snapshot()
            if self.capital_limit_eur is not None and account.net_deposits > self.capital_limit_eur:
                raise Rejected("Capital existant supérieur au profil autorisé")
            self.risk.rearm(account, command.panic_generation)
            self.memory, self.inflight, self.previous_price = None, None, None
            self.safety_error = ""
            self.status = f"Agent réactivé — session {self.risk.state.session_number}"
            self.risk.ready = True
            self.save()
            self.publish({"notice": self.status})

    def _withdrawal_available(self) -> bool:
        return not self.risk.state.liquidation_required and (
            not self.panic.is_set() or self.risk.state.liquidation_completed
        )

    def _interrupt_cashout(self, message: str) -> None:
        # This branch is reached strictly before transfer; no ambiguous submission.
        self.store.put("cashout_pending", "")
        self.store.log("cashout_interrupted", message)
        raise Rejected(message)

    def _fresh(self, tick: Tick | None) -> bool:
        return tick is not None and tick.symbol == "DEMO/EUR" and 0 <= time.time() - tick.timestamp < 3

    def cash_out(self, command: Command) -> None:
        """Persist intent, sell worst return first, then debit simulated settled cash.

        Heuristic, not optimal execution: mock uses identical costs per asset.
        A live adapter needs market depth, partial fills and settlement tracking.
        On uncertainty suspend and reconcile; never silently repeat a transfer.
        """
        if self.broker.completed(command):
            return
        if self.store.get("cashout_pending"):
            raise Rejected("Un retrait interrompu doit être rapproché avant une nouvelle demande")
        tick = self.mailbox.get()
        if self._fresh(tick):
            assert tick is not None
            self.broker.mark(tick)
        self.store.put("cashout_pending", command.model_dump_json())
        self.broker.cancel_all()
        account = self.broker.snapshot()
        if account.open_orders:
            raise RuntimeError("Cancellation not confirmed")
        proceeds = account.cash + account.exposure * (1 - SLIPPAGE) * (1 - FEE)
        if command.amount > proceeds:
            self.store.put("cashout_pending", "")
            raise ValueError("Retrait supérieur au produit net liquidable")
        ranked = sorted(
            account.positions,
            key=lambda s: (
                account.prices[s] / account.positions[s].average - 1 if account.positions[s].average else D(0)
            ),
        )
        for symbol in ranked:
            if not self._withdrawal_available() or self.stop.is_set():
                self._interrupt_cashout("Liquidation en cours : retrait à réessayer après sa fin")
            account = self.broker.snapshot()
            missing = command.amount - account.cash
            if missing <= 0:
                break
            if not self._fresh(self.mailbox.get()):
                self._interrupt_cashout("Cours périmés : réessayer le retrait après rétablissement du flux")
            net_price = account.prices[symbol] * (1 - SLIPPAGE) * (1 - FEE)
            # Tiny extra quantity prevents Decimal division rounding leaving insufficient cash.
            quantity = min(account.positions[symbol].quantity, missing / net_price + D("0.00000001"))
            self.broker.execute(Order(symbol=symbol, side="sell", quantity=quantity), self.risk.reduce_only)
        if not self._withdrawal_available():
            self._interrupt_cashout("Liquidation en cours : retrait à réessayer après sa fin")
        self.risk.observe(self.broker.snapshot())  # Price performance before the external flow.
        if not self._withdrawal_available():
            self._interrupt_cashout("Retrait interrompu par la limite de risque")
        self.broker.transfer(command)  # Paper only, no actual bank transaction.
        self.store.put("cashout_pending", "")
        self.memory, self.inflight = None, None
        self.status = f"Retrait simulé terminé : {command.amount} EUR"

    def _command(self, command: Command | ResumeCommand) -> None:
        with self.operations:
            if isinstance(command, ResumeCommand):
                self.resume(command)
                return
            if self.stop.is_set() or not self.recovered.is_set():
                raise Rejected("Compte non réconcilié ou application en cours de fermeture")
            if command.kind == "withdraw":
                if not self._withdrawal_available():
                    raise Rejected("Liquidation en cours : retrait disponible après sa fin")
            elif self.panic.is_set() or not self.risk.ready:
                raise Rejected("Recovery / panic")
            if command.kind == "deposit":
                account = self.broker.snapshot()
                if (
                    self.capital_limit_eur is not None
                    and not self.broker.completed(command)
                    and account.net_deposits + command.amount > self.capital_limit_eur
                ):
                    raise Rejected("Dépôt supérieur au capital autorisé du profil")
                self.risk.observe(self.broker.snapshot())
                if self.panic.is_set():
                    raise Rejected("Dépôt interrompu par la limite de risque")
                self.broker.transfer(command)
                self.memory, self.inflight = None, None
                self.status = f"Dépôt simulé : {command.amount} EUR"
            else:
                self.cash_out(command)
            self.risk.observe(self.broker.snapshot())
            self.save()
            self.publish({"notice": self.status})

    def _decision(self, action: int, tick: Tick) -> None:
        account = self.broker.snapshot()
        quantity = account.positions[tick.symbol].quantity if tick.symbol in account.positions else D(0)
        target = (
            quantity if action == 0 else (account.equity * D(".09") / tick.price if action == 1 else D(0))
        )
        delta = target - quantity
        # Paper dust threshold: allow the 0.90 EUR target for a 10 EUR demo.
        # Live lot sizes and minimum notionals must come from the broker adapter.
        if abs(delta) * tick.price < D("0.01"):
            return
        order = Order(symbol=tick.symbol, side="buy" if delta > 0 else "sell", quantity=abs(delta))
        self.store.log("intent", order.model_dump_json())
        self.broker.execute(order, self.risk.validate)

    def cycle(self) -> None:
        with self.operations:
            tick = self.mailbox.get()
            if not self._fresh(tick):
                self.memory, self.inflight = None, None
                self.status = "Flux périmé — trading bloqué"
                return
            assert tick is not None
            self.broker.mark(tick)
            account = self.broker.snapshot()
            dd = self.risk.observe(account, sample=tick.timestamp > self.last_metric_tick)
            self.last_metric_tick = tick.timestamp
            if self.status.startswith("Flux périmé"):
                self.status = "PAPER — flux rétabli"
            if self.inference_fault:
                self.report(dd)
                return
            try:
                identifier, action, memory, error = self.responses.get_nowait()
            except queue.Empty:
                pass
            else:
                if error:
                    raise RuntimeError(f"Inference failed: {error}")
                if self.inflight and identifier == self.inflight[0]:
                    _, revision, submitted = self.inflight
                    self.inflight = None
                    if revision == account.revision and time.monotonic() - submitted < 3:
                        self.memory = memory
                        if not self.risk.state.suspended and not self.panic.is_set():
                            self._decision(action, tick)
                    else:
                        self.memory = None
            if self.inflight and time.monotonic() - self.inflight[2] > 10:
                raise RuntimeError("Inference timeout")
            if (
                self.inflight is None
                and tick.timestamp > self.last_tick
                and account.equity > 0
                and not self.risk.state.suspended
                and not self.panic.is_set()
            ):
                account = self.broker.snapshot()
                quantity = (
                    account.positions[tick.symbol].quantity if tick.symbol in account.positions else D(0)
                )
                obs = features(
                    float(tick.price),
                    self.previous_price or float(tick.price),
                    float(quantity),
                    float(account.cash),
                )
                identifier = str(tick.timestamp)
                self.requests.put_nowait((identifier, obs.tolist(), self.memory))
                self.inflight = (identifier, account.revision, time.monotonic())
                self.previous_price, self.last_tick = float(tick.price), tick.timestamp
            self.report(dd)

    def report(self, dd: float | None = None) -> None:
        account = self.broker.snapshot()
        returns = np.asarray(self.risk.state.returns)
        # Unannualized sample Sharpe. Live annualization needs fixed sampling/calendar.
        sharpe = (
            float(returns.mean() / returns.std(ddof=1)) if len(returns) > 2 and returns.std() > 1e-12 else 0.0
        )
        chart_sample = time.monotonic() - self.last_metric_saved >= 5
        if chart_sample:
            self.store.record_metric(time.time(), str(account.equity), self.risk.state.nav)
            self.last_metric_saved = time.monotonic()
        if self.fills_revision != account.revision:
            self.recent_fills = self.store.recent_fills()
            self.fills_revision = account.revision
        history = self.store.history() if self.send_history else None
        self.send_history = False
        self.publish(
            {
                "status": self.safety_error
                or (
                    (
                        self.risk.state.reason
                        + (" — positions liquidées" if self.risk.state.liquidation_completed else "")
                    )
                    if self.risk.state.suspended
                    else self.status
                ),
                "loss_pct": float(max(D(0), 1 - D(self.risk.state.nav) / D(self.risk.state.session_nav))),
                "session_number": self.risk.state.session_number,
                "suspended": self.risk.state.suspended,
                "withdraw_available": self.recovered.is_set() and self._withdrawal_available(),
                "history": history,
                "chart_equity": float(account.equity) if chart_sample else None,
                "fills": self.recent_fills,
                "equity": float(account.equity),
                "cash": float(account.cash),
                "pnl": float(account.equity - account.net_deposits),
                "dd": dd,
                "sharpe": sharpe,
                "positions": {s: float(p.quantity * account.prices[s]) for s, p in account.positions.items()},
            }
        )

    def initialize_demo(self) -> None:
        """Fund only a pristine paper account, never refill losses or withdrawals."""
        with self.operations:
            account = self.broker.snapshot()
            if (
                self.demo_initial_capital is not None
                and account.revision == 0
                and account.net_deposits == 0
                and account.equity == 0
                and not self.risk.state.suspended
                and not self.panic.is_set()
            ):
                self._command(
                    Command(id="demo-initial-capital-v1", kind="deposit", amount=self.demo_initial_capital)
                )

    def reconcile_cashout(self) -> None:
        """Do not repeat a transfer completed before the engine checkpoint crashed."""
        pending = self.store.get("cashout_pending")
        if pending:
            command = Command.model_validate_json(pending)
            if self.broker.completed(command):
                self.store.put("cashout_pending", "")
                self.store.log("cashout_recovered", command.id)
            else:
                self.risk.suspend("Retrait interrompu : rapprochement opérateur requis")

    def write_report(self) -> None:
        try:
            with self.operations:
                self.save()
                path = export_report(self.store)
            self.publish({"report_path": str(path.resolve())})
        except Exception:
            log.exception("Report export failed")
            self.publish({"report_error": "Échec de l'export du bilan — consulter les logs"})

    def run(self) -> None:
        try:
            self.recover()
            self.reconcile_cashout()
            self.initialize_demo()
            while not self.stop.is_set():
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        self._command(command)
                    except (ValueError, Rejected) as error:
                        self.publish({"notice": str(error)})
                        self.store.log("command_rejected", str(error))
                    except Exception as error:
                        log.exception("Command failed; controls remain available")
                        self.risk.suspend(str(error))
                        self.publish({"notice": f"Opération échouée : {error}"})
                try:
                    self.cycle()
                except Rejected:
                    self.report()
                except Exception as error:
                    log.exception("Inference/market cycle halted; controls remain available")
                    self.inference_fault = True
                    self.memory, self.inflight = None, None
                    self.risk.suspend(str(error))
                    self.publish({"notice": f"Trading suspendu : {error}. Les retraits restent disponibles."})
                if self.export_requested.is_set():
                    self.export_requested.clear()
                    self.write_report()
                self.save()  # Checkpoint each cycle, including recurrent memory and risk baseline.
                self.report()
                self.stop.wait(0.5)
        except Exception as error:
            log.exception("Engine halted")
            self.risk.suspend(str(error))
            self.publish({"status": f"SUSPENDU : {error}"})
        finally:
            self.risk.ready = False
            try:
                with self.operations:
                    self.save()
                    self.write_report()
            except Exception:
                log.exception("Final persistence/report failed")

    def safety_loop(self) -> None:
        """Separate thread, never waits for neural inference; broker I/O must be bounded."""
        while not self.stop.is_set() or (self.panic.is_set() and not self.risk.state.liquidation_completed):
            if not self.panic.wait(0.1):
                continue
            if self.risk.state.liquidation_completed:
                self.stop.wait(0.1)
                continue
            try:
                with self.operations:
                    if not self.panic.is_set():
                        continue
                    self.risk.request_liquidation("PANIC — agent désactivé")
                    # Recovery barrier applies even if panic was pressed during startup.
                    if not self.recovered.is_set():
                        self.broker.snapshot()
                    self.broker.cancel_all()
                    account = self.broker.snapshot()
                    if account.open_orders:
                        raise RuntimeError("Ordres encore ouverts après annulation")
                    for symbol, position in account.positions.items():
                        self.broker.execute(
                            Order(symbol=symbol, side="sell", quantity=position.quantity),
                            self.risk.reduce_only,
                        )
                    final_account = self.broker.snapshot()
                    if final_account.positions or final_account.open_orders:
                        raise RuntimeError("Liquidation incomplète")
                    self.risk.observe(self.broker.snapshot())
                    self.store.log("panic", "flat confirmed")
                    self.risk.complete_liquidation()
                    self.safety_error = ""
                    self.status = self.risk.state.reason + " — positions liquidées"
                    self.save()
                    self.write_report()
                    self.publish({"status": self.status})
            except Exception as error:
                log.exception("Emergency liquidation failed")
                self.safety_error = f"PANIC ÉCHEC : {error} — intervention requise"
                self.publish({"status": self.safety_error})
                # Remain latched. Retry on next cycle while the app is running.
                if self.stop.wait(1):
                    break
