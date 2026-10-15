from pathlib import Path

import numpy as np
import pytest
import torch

from autotrader.ml.evaluate import EvaluationConfig, evaluate, load_csv, passive_policy, simulate
from autotrader.ml.model import SCHEMA, ActorCritic


def test_cash_has_no_artificial_profit_or_cost():
    result = simulate(np.linspace(100, 200, 50), lambda _: 0, EvaluationConfig())
    assert result["net_return"] == 0
    assert result["fees_eur"] == 0
    assert result["fills"] == 0


def test_round_trip_fees_including_terminal_close():
    config = EvaluationConfig(fee_rate=0.001, slippage=0)
    result = simulate(np.full(20, 100.0), passive_policy(), config)
    assert result["fills"] == 2
    assert result["fees_eur"] == pytest.approx(0.9 * 0.001 * 2)
    assert result["net_pnl_eur"] == pytest.approx(-result["fees_eur"])


def test_policy_cannot_capture_move_before_execution():
    prices = np.array([100.0, 100.0, 200.0, 200.0, 200.0])
    result = simulate(prices, passive_policy(), EvaluationConfig(fee_rate=0, slippage=0))
    assert result["net_pnl_eur"] == pytest.approx(0)


def test_risk_stop_cancels_all_future_decisions_and_closes():
    prices = np.array([100.0, 100.0, 100.0, 1.0, 1.0, 100.0])
    result = simulate(prices, lambda _: 1, EvaluationConfig(fee_rate=0, slippage=0))
    assert result["risk_stop"]
    assert result["fills"] == 2
    assert result["net_return"] == pytest.approx(-0.0891)  # Gaps can exceed 5%.


def test_minimum_costs_are_not_silently_ignored():
    prices = np.full(20, 100.0)
    result = simulate(prices, passive_policy(), EvaluationConfig(minimum_fee_eur=2))
    # 0.90 EUR target with a 2 EUR fee breaches 10% of resulting equity.
    assert result["rejected"] == 1
    assert result["fills"] == 0
    assert result["net_pnl_eur"] == 0


def test_csv_rejects_unsorted_and_nonfinite(tmp_path: Path):
    path = tmp_path / "prices.csv"
    path.write_text("timestamp,price_eur\n2026-01-01T00:00:00,100\n")
    with pytest.raises(ValueError, match="UTC"):
        load_csv(path)
    path.write_text("timestamp,price_eur\n" + "2026-01-01T00:00:00Z,100\n" * 4)
    with pytest.raises(ValueError, match="chronological"):
        load_csv(path)
    path.write_text(
        "timestamp,price_eur\n"
        + "".join(f"2026-01-0{i + 1}T00:00:00Z,{'nan' if i == 3 else 100}\n" for i in range(4))
    )
    with pytest.raises(ValueError, match="finite"):
        load_csv(path)


def test_report_never_promotes_synthetic_policy_and_preserves_weights(tmp_path: Path):
    torch.set_num_threads(1)
    path = tmp_path / "policy.pt"
    torch.save({"schema": SCHEMA, "weights": ActorCritic().state_dict()}, path)
    original = path.read_bytes()
    report = evaluate(path, np.full(96, 100.0), EvaluationConfig(), "synthetic")
    assert report["live_qualified"] is False
    assert report["diagnostic_screen_passed"] is False
    assert [(w["start_index"], w["end_index_exclusive"]) for w in report["windows"]] == [
        (0, 32),
        (32, 64),
        (64, 96),
    ]
    assert report["inference"]["p95_ms"] > 0
    assert path.read_bytes() == original
