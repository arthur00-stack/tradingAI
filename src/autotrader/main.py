"""Lifecycle: Qt main thread, feed + engine + safety threads, isolated inference."""

import argparse
import asyncio
import logging
import multiprocessing as mp
import os
import queue
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from autotrader.broker.paper import PaperBroker
from autotrader.core.engine import Engine
from autotrader.core.feed import Mailbox, paper_feed, websocket_feed
from autotrader.core.panic import PanicEvent
from autotrader.core.profile import TradingProfile
from autotrader.core.risk import RiskManager
from autotrader.core.security import private_file
from autotrader.core.store import Store
from autotrader.ml.worker import inference_worker


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--demo", action="store_true", help="Start the funded synthetic demo automatically")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--profile", type=Path, help="Validated IBKR France paper-only profile")
    parser.add_argument("--websocket", help="Normalized DEMO/EUR gateway URL; execution remains paper")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if not (root / "pyproject.toml").is_file():
        root = Path.home() / ".local/share/autotrader"
    if args.demo:
        from autotrader.ml.demo import prepare_demo

        args.checkpoint = args.checkpoint or prepare_demo(root / "var/demo-synthetique")
        args.database = args.database or root / "var/demo-synthetique/paper.sqlite3"
    args.database = args.database or Path("var/paper.sqlite3")
    profile = TradingProfile.load(args.profile) if args.profile else TradingProfile()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s")
    # Enforce single account writer for this local deployment (POSIX/Linux).
    import fcntl

    args.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(args.database.with_suffix(".lock"))
    lockfile = args.database.with_suffix(".lock").open("r+")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another instance owns this paper account")

    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from autotrader.gui.window import Window

    class Bridge(QObject):
        update = pyqtSignal(object)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)
    bridge = Bridge()
    stop, panic = threading.Event(), PanicEvent()
    mailbox = Mailbox()
    store = Store(args.database)
    store.put("market_source", "external-normalized-feed" if args.websocket else "synthetic")
    broker = PaperBroker(store)
    risk = RiskManager(store, panic, max_total_loss_eur=profile.max_total_loss_eur)
    ctx = mp.get_context("spawn")
    requests, responses = ctx.Queue(maxsize=2), ctx.Queue(maxsize=2)
    process = ctx.Process(
        target=inference_worker,
        args=(requests, responses, str(args.checkpoint) if args.checkpoint else None),
        name="inference",
        daemon=True,
    )
    window = Window(panic)
    window.amount.setValue(float(profile.capital_eur))
    engine = Engine(
        broker,
        store,
        risk,
        mailbox,
        stop,
        panic,
        requests,
        responses,
        bridge.update.emit,
        args.checkpoint,
        capital_limit_eur=profile.capital_eur,
        demo_initial_capital=profile.capital_eur if args.demo else None,
        inference_alive=process.is_alive,
    )
    # PyQt runtime supports this argument; its bundled stub omits the overload.
    bridge.update.connect(window.update_snapshot, Qt.ConnectionType.QueuedConnection)  # type: ignore[call-arg]

    def submit(command: Any) -> None:
        try:
            engine.submit(command)
        except queue.Full:
            window.update_snapshot({"notice": "File de commandes pleine — demande refusée"})

    def resume_agent() -> None:
        try:
            engine.submit_resume()
        except queue.Full:
            window.update_snapshot({"notice": "File pleine — réessayer la réactivation"})

    window.resume_requested.connect(resume_agent)
    window.command.connect(submit)
    window.export_report.connect(engine.export_requested.set)
    initial = float(broker.snapshot().prices.get("DEMO/EUR", 100))

    def feed() -> None:
        if args.websocket:
            asyncio.run(websocket_feed(args.websocket, mailbox, stop))
        else:
            paper_feed(mailbox, stop, initial)

    threads = [
        threading.Thread(target=feed, name="data-feed"),
        threading.Thread(target=engine.run, name="trading-engine"),
        threading.Thread(target=engine.safety_loop, name="safety"),
    ]
    shutdown_at: float | None = None

    def begin_shutdown() -> None:
        nonlocal shutdown_at
        if shutdown_at is None:
            shutdown_at = time.monotonic()
            stop.set()
            try:
                requests.put_nowait(None)
            except queue.Full:
                pass

    def poll() -> None:
        if shutdown_at is None:
            if process.exitcode is not None and not risk.state.suspended:
                risk.suspend("Processus d'inférence arrêté")
                window.update_snapshot({"status": risk.state.reason})
            return
        if process.is_alive() and time.monotonic() - shutdown_at > 3:
            process.terminate()
        if all(not thread.is_alive() for thread in threads) and not process.is_alive():
            process.join(timeout=0)
            window.can_close = True
            window.close()
        elif time.monotonic() - shutdown_at > 10:
            window.status.setText("Arrêt retardé : worker bloqué, processus conservé pour diagnostic")

    window.shutdown.connect(begin_shutdown)
    signal.signal(signal.SIGINT, lambda *_: window.close())
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    process.start()
    for thread in threads:
        thread.start()
    window.show()
    exit_code = app.exec()
    begin_shutdown()
    for thread in threads:
        thread.join(timeout=5)
    if process.is_alive():
        process.terminate()
    process.join(timeout=3)
    for channel in (requests, responses):
        channel.cancel_join_thread()
        channel.close()
    lockfile.close()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
