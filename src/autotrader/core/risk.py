"""Hard constraints outside the policy; sticky suspension survives restarts."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, RLock

from pydantic import Field

from autotrader.core.panic import PanicEvent
from autotrader.core.store import Store
from autotrader.core.types import Account, D, Order, Record


class Rejected(RuntimeError):
    pass


class RiskState(Record):
    day: str = ""
    peak: str = "0"
    flows: str = "0"
    suspended: bool = False
    reason: str = ""
    returns: list[float] = Field(default_factory=list)
    previous: str = "0"
    accounting_version: int = 2
    nav: str = "1"
    peak_nav: str = "1"
    last_equity: str = "0"
    sampled_nav: str = "1"
    session_nav: str = "1"
    session_number: int = 1
    liquidation_required: bool = False
    liquidation_completed: bool = False


class RiskManager:
    def __init__(
        self,
        store: Store,
        panic: Event,
        today: Callable[[], str] = lambda: datetime.now(UTC).date().isoformat(),
        max_total_loss_eur: Decimal | None = None,
    ) -> None:
        self.store, self.panic, self.today = store, panic, today
        if max_total_loss_eur is not None and (not max_total_loss_eur.is_finite() or max_total_loss_eur <= 0):
            raise ValueError("Loss budget must be finite and positive")
        self.max_total_loss_eur = max_total_loss_eur
        self.lock = RLock()
        raw = store.get("risk")
        self.state = RiskState.model_validate_json(raw) if raw else RiskState()
        # Legacy checkpoints do not contain enough flow timing to reconstruct NAV.
        # Retain their suspension and require reconciliation rather than inventing history.
        if raw and '"accounting_version"' not in raw:
            self.suspend("Migration risque v2 : référence de performance à rapprocher")
        if self.state.liquidation_required:
            self.panic.set()
        self.ready = False

    def suspend(self, reason: str) -> None:
        with self.lock:
            self.state = self.state.model_copy(update={"suspended": True, "reason": reason})
            self.save()

    def request_liquidation(self, reason: str) -> None:
        """Persist the stop before execution; a crash must not lose the request."""
        with self.lock:
            if self.state.liquidation_completed:
                return
            if not self.state.liquidation_required:
                self.state = self.state.model_copy(
                    update={
                        "suspended": True,
                        "reason": reason,
                        "liquidation_required": True,
                    }
                )
            try:
                self.save()
            finally:
                self.panic.set()

    def complete_liquidation(self) -> None:
        """Called only after the broker confirms no positions or pending orders."""
        with self.lock:
            self.state = self.state.model_copy(
                update={
                    "suspended": True,
                    "liquidation_required": False,
                    "liquidation_completed": True,
                }
            )
            self.save()

    def save(self) -> None:
        self.store.put("risk", self.state.model_dump_json())

    def observe(self, account: Account, *, sample: bool = False) -> float:
        with self.lock:
            s = self.state
            equity = account.equity
            delta = account.net_deposits - D(s.flows)
            # Chain-link returns with external flows at interval end. The engine
            # observes immediately before transfers, so a deposit cannot hide loss.
            last_equity, nav = D(s.last_equity), D(s.nav)
            before_flow = equity - delta
            if before_flow < 0:
                self.suspend("Flux incohérent : equity avant transfert négative")
                raise Rejected(self.state.reason)
            if last_equity > 0:
                nav *= before_flow / last_equity
            peak_nav = D(s.peak_nav)
            if s.day != self.today():
                peak_nav = nav
            peak_nav = max(peak_nav, nav)
            dd_decimal = (peak_nav - nav) / peak_nav if peak_nav > 0 else D(0)
            returns = list(s.returns)
            sampled_nav = D(s.sampled_nav)
            if sample and sampled_nav > 0:
                returns.append(float(nav / sampled_nav - 1))
            self.state = s.model_copy(
                update={
                    "day": self.today(),
                    "peak": str(equity),  # Legacy field; drawdown uses peak_nav.
                    "flows": str(account.net_deposits),
                    "previous": str(equity),
                    "returns": returns[-3600:],
                    "nav": str(nav),
                    "peak_nav": str(peak_nav),
                    "last_equity": str(equity),
                    "sampled_nav": str(nav if sample else sampled_nav),
                }
            )
            dd = float(dd_decimal)
            total_loss = max(D(0), 1 - nav / D(s.session_nav))
            if total_loss.quantize(D("1e-18")) >= D("0.05"):
                self.request_liquidation("ARRÊT : perte de la session >= 5 %")
            elif dd_decimal.quantize(D("1e-18")) >= D("0.05"):
                self.request_liquidation("ARRÊT : drawdown journalier >= 5 %")
            if (
                self.max_total_loss_eur is not None
                and account.net_deposits - equity >= self.max_total_loss_eur
            ):
                # PnL includes realized/unrealized loss and all broker-booked fees.
                # Detection triggers liquidation, it does NOT guarantee the exit price.
                self.request_liquidation(f"ARRÊT : perte nette cumulée >= {self.max_total_loss_eur} EUR")
            return dd

    def rearm(self, account: Account, panic_generation: int = 0) -> None:
        """Explicit new 5% session; preserve lifetime NAV, PnL and absolute loss budget."""
        with self.lock:
            if not self.state.suspended and not self.panic.is_set():
                raise Rejected("L'agent est déjà actif")
            self.observe(account)
            if self.state.liquidation_required or (
                self.panic.is_set() and not self.state.liquidation_completed
            ):
                raise Rejected("Liquidation en cours : réactivation indisponible")
            if account.open_orders or account.equity <= 0 or D(self.state.nav) <= 0:
                raise Rejected("Compte vide ou ordres non réconciliés")
            if account.exposure > account.equity * D(".10"):
                raise Rejected("Exposition encore supérieure à 10 %")
            if (
                self.max_total_loss_eur is not None
                and account.net_deposits - account.equity >= self.max_total_loss_eur
            ):
                raise Rejected("Limite globale de perte atteinte : elle n'est pas réinitialisée")
            if isinstance(self.panic, PanicEvent) and self.panic.generation != panic_generation:
                raise Rejected("Un nouveau PANIC a priorité sur la réactivation")
            previous = self.state
            updated = previous.model_copy(
                update={
                    "suspended": False,
                    "reason": "",
                    "liquidation_required": False,
                    "liquidation_completed": False,
                    "session_nav": previous.nav,
                    "peak_nav": previous.nav,
                    "day": self.today(),
                    "session_number": previous.session_number + 1,
                }
            )
            # Audit and new reference commit together. Do not erase account history.
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO state VALUES ('risk',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (updated.model_dump_json(),),
                )
                db.execute(
                    "INSERT INTO journal(kind,payload) VALUES ('manual_rearm',?)",
                    (previous.model_dump_json(),),
                )
            self.state = updated
            if isinstance(self.panic, PanicEvent):
                if not self.panic.clear_if_generation(panic_generation):
                    self.request_liquidation("PANIC reçu pendant la réactivation")
                    raise Rejected("Un nouveau PANIC a interrompu la réactivation")
            else:
                self.panic.clear()

    def validate(self, account: Account, order: Order) -> None:
        with self.lock:
            self.observe(account)
            if not self.ready or self.panic.is_set() or self.state.suspended:
                raise Rejected(self.state.reason or "Recovery / panic")
            if account.open_orders:
                self.suspend("Ordres en attente : réservations non implémentées")
                raise Rejected(self.state.reason)
            price = account.prices[order.symbol]
            if order.side == "sell":
                self.reduce_only(account, order)
                return
            notional = order.quantity * price
            # Conservative 1% execution buffer, above the mock fee+slippage.
            future_equity = account.equity - notional * D("0.01")
            if (
                account.exposure + notional * D("1.01") > future_equity * D("0.10")
                or notional * D("1.01") > account.cash
            ):
                self.suspend("Exposition totale projetée > 10 % ou cash insuffisant")
                raise Rejected(self.state.reason)

    @staticmethod
    def reduce_only(account: Account, order: Order) -> None:
        """Emergency/cash-out path may only reduce an existing long position."""
        position = account.positions.get(order.symbol)
        if order.side != "sell" or position is None or order.quantity > position.quantity:
            raise Rejected("Ordre de sécurité non réducteur")
