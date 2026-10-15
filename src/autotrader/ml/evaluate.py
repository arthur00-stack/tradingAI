"""Reproducible offline screening; never promotes a policy or touches an account.

CSV prices must be marked in EUR per unit of the traded asset. FX crosses quoted
in another currency require conversion upstream. This is a bar simulator, not an
IBKR fill model. There is no claim of profitability or production qualification.
"""

import argparse
import csv
import hashlib
import html
import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from autotrader.ml.environment import features
from autotrader.ml.model import ActorCritic, Hidden, load_policy

Prices = NDArray[np.float64]
Policy = Callable[[NDArray[np.float32]], int]


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    capital: float = Field(default=10, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, lt=0.1)
    minimum_fee_eur: float = Field(default=0, ge=0)
    slippage: float = Field(default=0.0005, ge=0, lt=0.1)
    latency_bars: int = Field(default=1, ge=1, le=100)
    max_loss_fraction: float = Field(default=0.05, gt=0, le=0.05)
    max_loss_eur: float = Field(default=1, gt=0, le=1)
    folds: int = Field(default=3, ge=2, le=20)


class RecurrentPolicy:
    """Same deterministic action and recurrent state as the inference worker."""

    def __init__(self, model: ActorCritic) -> None:
        self.model = model
        self.hidden: Hidden | None = None

    def __call__(self, observation: NDArray[np.float32]) -> int:
        with torch.inference_mode():
            logits, _, self.hidden = self.model(torch.from_numpy(observation).reshape(1, 1, 3), self.hidden)
        if not torch.isfinite(logits).all() or any(not torch.isfinite(h).all() for h in self.hidden):
            raise ValueError("Non-finite policy output")
        return int(logits[0, 0].argmax().item())


def validate_prices(prices: Prices) -> None:
    if prices.ndim != 1 or len(prices) < 4 or not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("At least four finite, positive chronological prices required")


def load_csv(path: Path) -> Prices:
    """Strict timestamp,price_eur CSV; reject unsorted/duplicate/unzoned records."""
    prices: list[float] = []
    previous: float | None = None
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["timestamp", "price_eur"]:
            raise ValueError("Expected CSV header: timestamp,price_eur")
        for row in reader:
            stamp = datetime.fromisoformat(row["timestamp"])
            if stamp.utcoffset() is None:
                raise ValueError("Timestamps must include a UTC offset")
            seconds = stamp.timestamp()
            if previous is not None and seconds <= previous:
                raise ValueError("Prices must be strictly chronological without duplicates")
            previous = seconds
            prices.append(float(row["price_eur"]))
    result = np.asarray(prices, dtype=np.float64)
    validate_prices(result)
    return result


def simulate(prices: Prices, policy: Policy, config: EvaluationConfig) -> dict[str, Any]:
    """Decision at t executes at t+latency; all exits include costs.

    Fresh account per fold. 9% target, 10% hard exposure limit, no borrowing or
    shorts; 5% session/peak and EUR loss stops cancel queued actions and close.
    Risk liquidation uses the current observed mark with adverse slippage, an
    optimistic assumption versus live outages/gaps. Terminal close is mandatory.
    """
    validate_prices(prices)
    cash, quantity = config.capital, 0.0
    fees, trades, rejected = 0.0, 0, 0
    peak = config.capital
    curve = [cash]
    pending: list[tuple[int, int]] = []
    stopped = False

    def execute(target: float, price: float, emergency: bool = False) -> None:
        nonlocal cash, quantity, fees, trades, rejected
        delta = target - quantity
        if abs(delta) < 1e-15 or (not emergency and abs(delta) * price < 0.01):
            return
        fill = price * (1 + config.slippage if delta > 0 else 1 - config.slippage)
        fee = max(abs(delta) * fill * config.fee_rate, config.minimum_fee_eur)
        new_cash = cash - delta * fill - fee
        new_equity = new_cash + target * price
        if not emergency and (
            new_cash < 0 or target < 0 or (delta > 0 and target * price > 0.1 * new_equity)
        ):
            rejected += 1
            return
        cash, quantity = new_cash, target
        fees += fee
        trades += 1

    for t in range(1, len(prices)):
        price = float(prices[t])
        equity = cash + quantity * price
        peak = max(peak, equity)
        if (
            equity <= config.capital * (1 - config.max_loss_fraction)
            or equity <= peak * (1 - config.max_loss_fraction)
            or config.capital - equity >= config.max_loss_eur
        ):
            stopped = True
            pending.clear()
            execute(0, price, emergency=True)
        if not stopped:
            due = [action for when, action in pending if when <= t]
            pending = [(when, action) for when, action in pending if when > t]
            for action in due:
                equity = cash + quantity * price
                target = quantity if action == 0 else (0.09 * equity / price if action == 1 else 0)
                execute(target, price)
            equity = cash + quantity * price
            peak = max(peak, equity)
            if (
                equity <= config.capital * (1 - config.max_loss_fraction)
                or equity <= peak * (1 - config.max_loss_fraction)
                or config.capital - equity >= config.max_loss_eur
            ):
                stopped = True
                pending.clear()
                execute(0, price, emergency=True)
            elif t < len(prices) - 1:
                action = policy(features(price, float(prices[t - 1]), quantity, cash))
                if action not in (0, 1, 2):
                    raise ValueError("Invalid policy action")
                pending.append((t + config.latency_bars, action))
        if t == len(prices) - 1:
            execute(0, price, emergency=True)
        curve.append(cash + quantity * price)
    values = np.asarray(curve)
    returns = np.diff(values) / np.maximum(values[:-1], 1e-12)
    volatility = float(returns.std(ddof=1))
    return {
        "net_return": float(values[-1] / config.capital - 1),
        "net_pnl_eur": float(values[-1] - config.capital),
        "max_drawdown": float(np.max(1 - values / np.maximum.accumulate(values))),
        "fees_eur": fees,
        "fills": trades,
        "rejected": rejected,
        "risk_stop": stopped,
        "sample_sharpe_not_annualized": float(returns.mean() / volatility) if volatility > 1e-12 else 0,
    }


def passive_policy() -> Policy:
    opened = False

    def decide(observation: NDArray[np.float32]) -> int:
        nonlocal opened
        if not opened:
            opened = True
            return 1
        return 0

    return decide


def benchmark(model: ActorCritic, samples: int = 300) -> dict[str, Any]:
    policy = RecurrentPolicy(model)
    observation = np.asarray([0, 0, 1], dtype=np.float32)
    for _ in range(30):
        policy(observation)
    elapsed = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        policy(observation)
        elapsed.append((time.perf_counter_ns() - start) / 1e6)
    return {
        "samples": samples,
        "cpu_threads": torch.get_num_threads(),
        "p50_ms": float(np.percentile(elapsed, 50)),
        "p95_ms": float(np.percentile(elapsed, 95)),
        "scope": "Model + features tensor + output checks; excludes IPC, feed, broker and engine polling",
    }


def evaluate(
    checkpoint: Path,
    prices: Prices,
    config: EvaluationConfig,
    source: Literal["synthetic", "user_csv"],
) -> dict[str, Any]:
    """Disjoint chronological windows, no fitting or model selection on test data.

    An external checkpoint's training overlap cannot be established here.
    Consequently these diagnostics never label it qualified for live trading.
    """
    validate_prices(prices)
    if len(prices) < config.folds * 32:
        raise ValueError("At least 32 observations per evaluation window required")
    torch.set_num_threads(1)
    model = load_policy(checkpoint)
    windows = np.array_split(prices, config.folds)
    results: list[dict[str, Any]] = []
    offset = 0
    for window in windows:
        candidates = {}
        for name, factory in (
            ("lstm_a2c", lambda: RecurrentPolicy(model)),
            ("cash", lambda: lambda observation: 0),
            ("passive_9pct", passive_policy),
        ):
            candidates[name] = simulate(window, factory(), config)
        stress = config.model_copy(
            update={
                "fee_rate": min(config.fee_rate * 2, 0.099),
                "minimum_fee_eur": config.minimum_fee_eur * 2,
                "slippage": min(config.slippage * 2, 0.099),
                "latency_bars": config.latency_bars + 1,
            }
        )
        results.append(
            {
                "start_index": offset,
                "end_index_exclusive": offset + len(window),
                "candidates": candidates,
                "stressed_lstm_a2c": simulate(window, RecurrentPolicy(model), stress),
            }
        )
        offset += len(window)
    passed = all(
        row["candidates"]["lstm_a2c"]["net_return"]
        > max(row["candidates"]["cash"]["net_return"], row["candidates"]["passive_9pct"]["net_return"])
        and row["stressed_lstm_a2c"]["net_return"] > 0
        and row["candidates"]["lstm_a2c"]["max_drawdown"] < 0.05
        and not row["candidates"]["lstm_a2c"]["risk_stop"]
        and not row["stressed_lstm_a2c"]["risk_stop"]
        and row["stressed_lstm_a2c"]["max_drawdown"] < 0.05
        for row in results
    )
    return {
        "schema": "offline-screen-v1",
        "source": source,
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "prices_sha256": hashlib.sha256(prices.astype("<f8").tobytes()).hexdigest(),
        "config": config.model_dump(),
        "observations": len(prices),
        "windows": results,
        "diagnostic_screen_passed": passed,
        "live_qualified": False,
        "training_overlap": "unverified; no out-of-sample claim for supplied checkpoint",
        "limitations": [
            "Synthetic data cannot demonstrate a market edge.",
            "EUR-marked single asset, fixed spread/slippage, no financing or variable liquidity.",
            "No daily calendar, currency conversion, settlement or live broker lot-size model.",
            "Independent funded windows, not a compounded continuous account.",
            "Risk exits at observed mark plus slippage; real losses can exceed stop thresholds.",
            "Do not repeatedly tune against these windows; reserve a new untouched final dataset.",
        ],
        "inference": benchmark(model),
    }


def render_report(report: dict[str, Any]) -> str:
    """Readable local report; escape all interpolated text, no remote assets."""
    rows = []
    for i, window in enumerate(report["windows"], 1):
        for name, result in {
            **window["candidates"],
            "lstm_costs_x2_latency_plus1": window["stressed_lstm_a2c"],
        }.items():
            rows.append(
                f"<tr><td>{i}</td><td>{html.escape(name)}</td>"
                f"<td>{result['net_return']:.3%}</td><td>{result['max_drawdown']:.3%}</td>"
                f"<td>{result['fees_eur']:.6f}</td><td>{result['fills']}</td></tr>"
            )
    status = (
        "Critères diagnostiques satisfaits"
        if report["diagnostic_screen_passed"]
        else "Critères diagnostiques non satisfaits"
    )
    return (
        "<!doctype html><html lang='fr'><meta charset='utf-8'>"
        "<title>Évaluation du modèle</title>"
        "<style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:20px}"
        "table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        "<h1>Évaluation du modèle — recherche uniquement</h1>"
        f"<p><strong>{status}. Aucune qualification pour le trading réel.</strong></p>"
        f"<p>Source : {html.escape(report['source'])}. "
        "Sur données synthétiques, aucun avantage de marché ne peut être démontré. "
        "Le recouvrement avec l'entraînement du modèle fourni n'est pas vérifié.</p>"
        f"<p>Chaque période commence avec {report['config']['capital']:g} € fictifs, exposition cible de 9 %, sans levier. "
        "Les frais et la clôture finale sont inclus. Les coûts sont ceux du simulateur, "
        "pas une reproduction de la tarification et de l'exécution IBKR.</p>"
        "<table><tr><th>Période</th><th>Stratégie</th><th>Rendement net</th>"
        "<th>Drawdown max.</th><th>Frais €</th><th>Exécutions</th></tr>" + "".join(rows) + "</table>"
        f"<p>Inférence CPU p95 : {report['inference']['p95_ms']:.3f} ms "
        "(hors communication, boucle moteur, flux et broker).</p>"
        "<p>Le fichier JSON associé contient les hypothèses, limites et empreintes de reproductibilité. "
        "Ce rapport ne modifie ni le modèle utilisé dans l'application, ni le compte.</p>"
        "</html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path("var/demo-synthetique/policy.pt"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--synthetic", action="store_true")
    group.add_argument("--csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("var/evaluation/report.json"))
    parser.add_argument("--minimum-fee-eur", type=float, default=0)
    args = parser.parse_args()
    if args.synthetic:
        rng = np.random.default_rng(20260930)
        prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.0005, 6000)))
        source: Literal["synthetic", "user_csv"] = "synthetic"
    else:
        prices = load_csv(args.csv)
        source = "user_csv"
    report = evaluate(args.checkpoint, prices, EvaluationConfig(minimum_fee_eur=args.minimum_fee_eur), source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    html_path = args.output.with_suffix(".html")
    html_path.write_text(render_report(report))
    print(f"Report: {args.output}; readable report: {html_path}")
    print(f"Diagnostic passed: {report['diagnostic_screen_passed']}; live qualified: False")
    print(f"Inference p95: {report['inference']['p95_ms']:.3f} ms")


if __name__ == "__main__":
    main()
