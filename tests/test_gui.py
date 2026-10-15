import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from threading import Event

from PyQt6.QtWidgets import QApplication, QPushButton

from autotrader.gui.window import Window


def test_gui_commands_and_panic():
    app = QApplication.instance() or QApplication([])
    panic = Event()
    window = Window(panic)
    commands = []
    window.command.connect(commands.append)
    window.send("withdraw")
    assert commands[0].kind == "withdraw"
    button = next(b for b in window.findChildren(QPushButton) if b.text().startswith("PANIC"))
    button.click()
    assert panic.is_set()
    window.update_snapshot(
        {
            "status": "PAPER",
            "equity": 100.0,
            "cash": 90.0,
            "pnl": 0.0,
            "dd": 0.01,
            "sharpe": 0.1,
            "positions": {"DEMO/EUR": 10.0},
        }
    )
    assert "100.00" in window.metrics.text()
    window.can_close = True
    window.close()
    app.processEvents()


def test_gui_history_fills_and_export_signal():
    app = QApplication.instance() or QApplication([])
    window = Window(Event())
    exports = []
    window.export_report.connect(lambda: exports.append(True))
    button = next(b for b in window.findChildren(QPushButton) if b.text() == "Exporter le bilan")
    button.click()
    assert exports == [True]
    data = {
        "status": "PAPER",
        "equity": 10.0,
        "cash": 9.0,
        "pnl": -0.001,
        "dd": 0.01,
        "sharpe": 0.1,
        "positions": {},
        "history": [10.0, 9.99],
        "fills": [
            {
                "order_id": "a",
                "timestamp": "2026-09-30T12:00:00Z",
                "side": "buy",
                "quantity": ".01",
                "price": "100",
                "fee": ".001",
            }
        ],
    }
    window.update_snapshot(data)
    assert list(window.history) == [10.0, 9.99]
    assert window.trades.rowCount() == 1
    window.update_snapshot({"report_path": "/tmp/bilan.html"})
    assert window.open_report.isEnabled()
    assert list(window.history) == [10.0, 9.99]  # Reports must not duplicate chart samples.
    window.can_close = True
    window.close()
    app.processEvents()


def test_reactivate_signal_and_withdraw_available_when_stopped():
    app = QApplication.instance() or QApplication([])
    window = Window(Event())
    resumed = []
    window.resume_requested.connect(lambda: resumed.append(True))
    window.update_snapshot({"status": "Arrêt", "suspended": True, "notice": "Retrait terminé"})
    window.resume_button.click()
    assert resumed == [True]
    assert window.withdraw.isEnabled()
    window.update_snapshot({"status": "Arrêt"})
    assert window.notice.text() == "Retrait terminé"
    window.can_close = True
    window.close()
    app.processEvents()
