"""Qt main thread only. Signals carry commands; workers never touch widgets."""

from collections import deque
from threading import Event
from typing import Any

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent, QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autotrader.core.types import Command, D


class Window(QMainWindow):
    command = pyqtSignal(object)
    shutdown = pyqtSignal()
    export_report = pyqtSignal()
    resume_requested = pyqtSignal()

    def __init__(self, panic: Event) -> None:
        super().__init__()
        self.setWindowTitle("Autotrader 0.2.1 — PAPER uniquement")
        self.resize(1100, 820)
        self.setStyleSheet(
            "QMainWindow {background:#f4f6f9;} QLabel {font-size:13px;} "
            "QPushButton {padding:8px;} QTableWidget {background:white;}"
        )
        self.can_close = False
        self.closing = False
        self.history: deque[float] = deque(maxlen=2000)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.status = QLabel("RECOVERY — attente de réconciliation")
        self.metrics = QLabel("Capital, PnL, exposition : en attente")
        self.metrics.setWordWrap(True)
        self.status.setWordWrap(True)
        layout.addWidget(QLabel("SIMULATION — aucun gain garanti | Arrêt et liquidation dès 5 % de perte"))
        layout.addWidget(self.status)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        layout.addWidget(self.metrics)
        self.plot = pg.PlotWidget(title="Valeur du portefeuille — EUR (échantillons ~5 s)")
        self.plot.setLabel("left", "EUR")
        self.plot.setLabel("bottom", "Échantillons conservés")
        self.curve = self.plot.plot(pen=pg.mkPen("#36cfaa", width=2))
        layout.addWidget(self.plot)
        self.positions = QLabel("Exposition par actif : aucune")
        layout.addWidget(self.positions)
        self.trades = QTableWidget(0, 5)
        self.trades.setHorizontalHeaderLabels(["Heure UTC", "Sens", "Quantité", "Prix exécuté", "Frais EUR"])
        header = self.trades.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.trades.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.trades.setMaximumHeight(160)
        layout.addWidget(self.trades)
        self.last_fill_ids: list[str] = []
        self.report_path: str | None = None
        self.report_label = QLabel("Le bilan sera enregistré automatiquement à la fermeture.")
        self.report_label.setWordWrap(True)
        self.report_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        report_row = QHBoxLayout()
        export = QPushButton("Exporter le bilan")
        export.clicked.connect(self.export_report.emit)
        self.open_report = QPushButton("Ouvrir le dernier bilan")
        self.open_report.setEnabled(False)
        self.open_report.clicked.connect(self.show_report)
        report_row.addWidget(export)
        report_row.addWidget(self.open_report)
        layout.addLayout(report_row)
        layout.addWidget(self.report_label)
        row = QHBoxLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0.01, 1_000_000_000)
        self.amount.setDecimals(2)
        self.amount.setValue(10_000)
        self.amount.setSuffix(" EUR")
        row.addWidget(self.amount)
        deposit = QPushButton("Déposer / initialiser (simulé)")
        self.withdraw = QPushButton("Smart Cash-Out (simulé)")
        deposit.clicked.connect(lambda: self.send("deposit"))
        self.withdraw.clicked.connect(lambda: self.send("withdraw"))
        row.addWidget(deposit)
        row.addWidget(self.withdraw)
        layout.addLayout(row)
        self.resume_button = QPushButton("Réactiver l’agent")
        self.resume_button.setEnabled(False)
        self.resume_button.clicked.connect(self.resume_requested.emit)
        layout.addWidget(self.resume_button)
        layout.addWidget(
            QLabel(
                "Réactivation manuelle : nouveau seuil de 5 % sur le capital restant. "
                "Limite globale de perte conservée."
            )
        )
        emergency = QPushButton("PANIC — annuler, liquider, désactiver")
        emergency.setStyleSheet("background:#b91c1c;color:white;font-weight:bold;padding:14px")
        emergency.clicked.connect(panic.set)
        layout.addWidget(emergency)
        layout.addWidget(
            QLabel(
                "Fermeture normale : sauvegarde et arrêt ; les positions restent ouvertes.\n"
                "Sharpe affiché : échantillons, non annualisé. Aucun virement réel."
            )
        )
        self.last: dict[str, Any] = {}

    def send(self, kind: str) -> None:
        if kind == "deposit":
            command = Command(kind="deposit", amount=D(str(self.amount.value())))
        else:
            command = Command(kind="withdraw", amount=D(str(self.amount.value())))
        self.command.emit(command)

    @pyqtSlot(object)
    def update_snapshot(self, data: dict[str, Any]) -> None:
        if data.get("report_path"):
            self.report_path = str(data["report_path"])
            self.report_label.setText("Bilan enregistré : " + self.report_path)
            self.open_report.setEnabled(True)
        if data.get("report_error"):
            self.report_label.setText(str(data["report_error"]))
        self.last.update({key: value for key, value in data.items() if value is not None})
        if "notice" in data:
            self.notice.setText(str(data["notice"]))
        if not self.closing:
            self.resume_button.setEnabled(bool(self.last.get("suspended", False)))
            self.status.setText(str(self.last.get("status", "")))
        if "equity" not in self.last:
            return
        d = self.last
        self.metrics.setText(
            f"Capital {d['equity']:,.2f} € | Cash {d['cash']:,.2f} € | "
            f"PnL net {d['pnl']:+,.4f} € | Perte session {d.get('session_number', 1)} : {d.get('loss_pct', 0):.2%} / 5 % | DD jour {d.get('dd', 0):.2%} | "
            f"Sharpe échantillons {d['sharpe']:.3f}"
        )
        self.positions.setText(
            "Exposition : "
            + (", ".join(f"{s}: {v:,.2f} €" for s, v in d["positions"].items()) or "aucune position")
        )
        if data.get("history") is not None:
            self.history.clear()
            self.history.extend(data["history"])
        elif data.get("chart_equity") is not None:
            self.history.append(data["chart_equity"])
        self.curve.setData(list(self.history))
        fills = data.get("fills", [])
        identifiers = [fill["order_id"] for fill in fills]
        if "fills" in data and identifiers != self.last_fill_ids:
            self.last_fill_ids = identifiers
            self.trades.setRowCount(len(fills))
            for row, fill in enumerate(fills):
                values = [
                    fill["timestamp"][11:19],
                    "Achat" if fill["side"] == "buy" else "Vente",
                    f"{float(fill['quantity']):.6f}",
                    f"{float(fill['price']):.5f}",
                    f"{float(fill['fee']):.6f}",
                ]
                for column, value in enumerate(values):
                    self.trades.setItem(row, column, QTableWidgetItem(value))

    def show_report(self) -> None:
        if self.report_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.report_path))

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if event is None:
            return
        if self.can_close:
            event.accept()
        else:
            event.ignore()
            if not self.closing:
                self.closing = True
                for button in self.findChildren(QPushButton):
                    if not button.text().startswith("PANIC"):
                        button.setEnabled(False)
                self.status.setText("Arrêt en cours — sauvegarde et attente des workers…")
                self.shutdown.emit()
