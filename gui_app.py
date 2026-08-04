import sys
import numpy as np
import pandas as pd
import pyqtgraph as pg

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import torch

from fetch_data import load_data
from trading_env import StockTradingEnv
from train_lstm import LSTMTradingActorCritic

# Style visuel Sombre Pro
pg.setConfigOption("background", "#0f172a")
pg.setConfigOption("foreground", "#cbd5e1")


class TradingWorker(QThread):
    """Thread gérant la simulation de trading."""

    update_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(list)

    def __init__(self, ticker="AAPL"):
        super().__init__()
        self.ticker = ticker
        self.history_logs = []

    def run(self):
        try:
            df = load_data(self.ticker, start="2023-01-01", end="2024-01-01")
            env = StockTradingEnv(df)
            model = LSTMTradingActorCritic(
                input_dim=5, hidden_dim=64, num_layers=2, action_dim=3
            )

            try:
                model.load_state_dict(torch.load("lstm_trading_model.pth"))
                model.eval()
            except Exception:
                pass

            obs, _ = env.reset()
            hidden = model.init_hidden(batch_size=1)
            done = False

            while not done:
                state_tensor = torch.tensor(obs, dtype=torch.float32).unsqueeze(
                    0
                )
                with torch.no_grad():
                    logits, value, hidden = model(state_tensor, hidden)

                action = torch.argmax(logits, dim=-1).item()
                next_obs, reward, done, _, info = env.step(action)

                current_row = env.df.iloc[env.current_step - 1]
                data_packet = {
                    "step": env.current_step,
                    "price": current_row["Close"],
                    "sma10": current_row["SMA_10"],
                    "sma30": current_row["SMA_30"],
                    "rsi": current_row["RSI"],
                    "net_worth": info["net_worth"],
                    "action": action,
                    "shares": env.shares_held,
                    "balance": info["balance"],
                }
                self.history_logs.append(data_packet)
                self.update_signal.emit(data_packet)
                self.msleep(80)
                obs = next_obs

            self.finished_signal.emit(self.history_logs)
        except Exception as e:
            print("Erreur Thread:", e)


class TradingDashboard(QMainWindow):
    """Interface graphique principale de Trading IA."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "IA Trading Pro - Dashboard & Live Tracker (V2 Pro)"
        )
        self.resize(1400, 850)
        self.setStyleSheet("""
            QMainWindow { background-color: #020617; }
            QGroupBox { 
                color: #94a3b8; font-weight: bold; border: 1px solid #1e293b; 
                border-radius: 8px; margin-top: 10px; font-size: 11px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPushButton { 
                background-color: #2563eb; color: white; font-weight: bold; 
                border-radius: 6px; padding: 8px 16px; border: none;
            }
            QPushButton:hover { background-color: #1d4ed8; }
            QPushButton:disabled { background-color: #334155; color: #64748b; }
            QComboBox { 
                background-color: #0f172a; color: #f8fafc; border: 1px solid #334155; 
                border-radius: 6px; padding: 5px 10px;
            }
            QTableWidget { 
                background-color: #0f172a; color: #f8fafc; gridline-color: #1e293b; 
                border: 1px solid #1e293b; border-radius: 6px;
            }
            QHeaderView::section { background-color: #1e293b; color: #94a3b8; font-weight: bold; border: none; }
            QLabel { color: #f8fafc; }
        """)

        self.history_price = []
        self.history_net_worth = []
        self.history_sma10 = []
        self.history_sma30 = []

        self._init_ui()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- PANNEAU GAUCHE ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        ctrl_group = QGroupBox("CONTROLES IA")
        ctrl_layout = QVBoxLayout(ctrl_group)

        self.ticker_selector = QComboBox()
        self.ticker_selector.addItems(
            ["AAPL", "NVDA", "TSLA", "MSFT", "BTC-USD"]
        )
        ctrl_layout.addWidget(QLabel("Actif à analyser :"))
        ctrl_layout.addWidget(self.ticker_selector)

        self.btn_run = QPushButton("🚀 Lancer le Trading IA")
        self.btn_run.clicked.connect(self.start_simulation)
        ctrl_layout.addWidget(self.btn_run)

        left_layout.addWidget(ctrl_group)

        metrics_group = QGroupBox("PERFORMANCE & RISQUE")
        metrics_layout = QVBoxLayout(metrics_group)

        self.lbl_net_worth = QLabel("Valeur Nette : $10,000.00")
        self.lbl_net_worth.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.lbl_net_worth.setStyleSheet("color: #4ade80;")

        self.lbl_action = QLabel("Action IA : NEUTRE")
        self.lbl_action.setFont(QFont("Arial", 10, QFont.Weight.Bold))

        self.lbl_sharpe = QLabel("Ratio Sharpe : N/A")
        self.lbl_drawdown = QLabel("Max Drawdown : N/A")

        metrics_layout.addWidget(self.lbl_net_worth)
        metrics_layout.addWidget(self.lbl_action)
        metrics_layout.addWidget(self.lbl_sharpe)
        metrics_layout.addWidget(self.lbl_drawdown)
        left_layout.addWidget(metrics_group)

        market_group = QGroupBox("TOP ACTIFS (LIVE)")
        market_layout = QVBoxLayout(market_group)

        self.table_market = QTableWidget(5, 3)
        self.table_market.setHorizontalHeaderLabels(
            ["Actif", "Prix", "Tendance IA"]
        )
        self.table_market.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        assets = [
            ("NVDA", "$128.50", "🟢 HAUSSIER"),
            ("AAPL", "$224.30", "🟢 HAUSSIER"),
            ("TSLA", "$210.10", "🔴 BAISSIER"),
            ("MSFT", "$448.90", "⚪ NEUTRE"),
            ("BTC-USD", "$64,200", "🟢 HAUSSIER"),
        ]
        for row, (ticker, price, trend) in enumerate(assets):
            self.table_market.setItem(row, 0, QTableWidgetItem(ticker))
            self.table_market.setItem(row, 1, QTableWidgetItem(price))
            item_trend = QTableWidgetItem(trend)
            if "🟢" in trend:
                item_trend.setForeground(QColor("#4ade80"))
            elif "🔴" in trend:
                item_trend.setForeground(QColor("#f87171"))
            self.table_market.setItem(row, 2, item_trend)

        market_layout.addWidget(self.table_market)
        left_layout.addWidget(market_group)

        splitter.addWidget(left_panel)

        # --- PANNEAU DROIT (GRAPHIQUES) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.plot_price = pg.PlotWidget(
            title="<b>Prix, Moyennes Mobiles & Signaux d'Achat/Vente ($)</b>"
        )
        self.plot_price.showGrid(x=True, y=True, alpha=0.2)
        self.curve_price = self.plot_price.plot(
            pen=pg.mkPen(color="#38bdf8", width=2)
        )
        self.curve_sma10 = self.plot_price.plot(
            pen=pg.mkPen(color="#f59e0b", width=1, style=Qt.PenStyle.DashLine)
        )
        self.curve_sma30 = self.plot_price.plot(
            pen=pg.mkPen(color="#ec4899", width=1, style=Qt.PenStyle.DashLine)
        )

        # Marqueurs d'Achat (Vert) et Vente (Rouge)
        self.scatter_trades = pg.ScatterPlotItem(size=12)
        self.plot_price.addItem(self.scatter_trades)

        right_layout.addWidget(self.plot_price)

        self.plot_portfolio = pg.PlotWidget(
            title="<b>Évolution du Capital ($)</b>"
        )
        self.plot_portfolio.showGrid(x=True, y=True, alpha=0.2)
        self.curve_net_worth = self.plot_portfolio.plot(
            pen=pg.mkPen(color="#4ade80", width=2)
        )
        right_layout.addWidget(self.plot_portfolio)

        splitter.addWidget(right_panel)
        splitter.setSizes([400, 1000])

    def start_simulation(self):
        self.btn_run.setEnabled(False)
        self.history_price.clear()
        self.history_net_worth.clear()
        self.history_sma10.clear()
        self.history_sma30.clear()
        self.scatter_trades.clear()

        ticker = self.ticker_selector.currentText()
        self.worker = TradingWorker(ticker=ticker)
        self.worker.update_signal.connect(self.update_gui)
        self.worker.finished_signal.connect(self.simulation_finished)
        self.worker.start()

    def update_gui(self, data):
        self.history_price.append(data["price"])
        self.history_sma10.append(data["sma10"])
        self.history_sma30.append(data["sma30"])
        self.history_net_worth.append(data["net_worth"])

        # Rafraîchissement des courbes
        self.curve_price.setData(self.history_price)
        self.curve_sma10.setData(self.history_sma10)
        self.curve_sma30.setData(self.history_sma30)
        self.curve_net_worth.setData(self.history_net_worth)

        # Ajout des marqueurs visuels sur la courbe
        step_idx = len(self.history_price) - 1
        if data["action"] == 1:  # Achat -> Vert
            self.scatter_trades.addPoints([{
                "pos": (step_idx, data["price"]),
                "brush": "#4ade80",
                "symbol": "t1",
            }])
        elif data["action"] == 2:  # Vente -> Rouge
            self.scatter_trades.addPoints([{
                "pos": (step_idx, data["price"]),
                "brush": "#f87171",
                "symbol": "t",
            }])

        # Mise à jour des textes
        self.lbl_net_worth.setText(f"Valeur Nette : ${data['net_worth']:,.2f}")
        actions_str = {
            0: "HOLD (ATTENDRE)",
            1: "🟢 ACHAT (BUY)",
            2: "🔴 VENTE (SELL)",
        }
        self.lbl_action.setText(
            f"Action IA : {actions_str.get(data['action'], 'NEUTRE')}"
        )

        # Calcul à la volée du Drawdown et Sharpe
        net_arr = np.array(self.history_net_worth)
        if len(net_arr) > 2:
            peak = np.maximum.accumulate(net_arr)
            dd = (net_arr - peak) / peak
            max_dd = np.min(dd) * 100

            returns = np.diff(net_arr) / net_arr[:-1]
            sharpe = (
                (np.mean(returns) / (np.std(returns) + 1e-8)) * np.sqrt(252)
                if np.std(returns) > 0
                else 0
            )

            self.lbl_drawdown.setText(f"Max Drawdown : {max_dd:.2f}%")
            self.lbl_sharpe.setText(f"Ratio Sharpe : {sharpe:.2f}")

    def simulation_finished(self, logs):
        self.btn_run.setEnabled(True)

        # Export automatique des données en CSV
        df_log = pd.DataFrame(logs)
        df_log.to_csv("trade_log.csv", index=False)

        QMessageBox.information(
            self,
            "Terminé",
            "Simulation terminée avec succès !\nHistorique complet exporté sous 'trade_log.csv'.",
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TradingDashboard()
    window.show()
    sys.exit(app.exec())