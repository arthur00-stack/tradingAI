"""Local, self-contained paper account reports. No network or external scripts."""

import csv
import html
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autotrader.core.security import private_database
from autotrader.core.store import Store
from autotrader.core.types import Account, D


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_report(store: Store) -> Path:
    """Export a coherent DB snapshot as HTML, JSON and operation CSV.

    Account equity is the last persisted market valuation, not liquidation cash.
    Fees are already deducted from equity and must not be subtracted twice.
    Old databases without fills retain their PnL but cannot reconstruct all fees.
    """
    with store.connect() as db:
        db.execute("BEGIN")
        raw = dict(db.execute("SELECT key,value FROM state"))
        operations = [
            json.loads(row[0]) for row in db.execute("SELECT request FROM operations ORDER BY rowid")
        ]
        fills = [json.loads(row[0]) for row in db.execute("SELECT payload FROM paper_fills ORDER BY id")]
        samples = [
            dict(zip(("time", "equity", "nav"), row, strict=True))
            for row in db.execute("SELECT at,equity,nav FROM metrics ORDER BY id")
        ]
    account = Account.model_validate_json(raw["broker"]) if "broker" in raw else Account()
    risk = json.loads(raw.get("risk", "{}"))
    orders = [operation for operation in operations if "side" in operation]
    fees = sum((D(fill["fee"]) for fill in fills), D(0))
    report: dict[str, Any] = {
        "mode": "PAPER",
        "market_source": raw.get("market_source", "unknown"),
        "generated_at": datetime.now(UTC).isoformat(),
        "warning": "Simulation uniquement. Aucun gain garanti. Frais synthétiques, non IBKR.",
        "equity_eur": str(account.equity),
        "cash_eur": str(account.cash),
        "net_deposits_eur": str(account.net_deposits),
        "net_pnl_eur": str(account.equity - account.net_deposits),
        "recorded_fees_eur": str(fees),
        "complete_fee_history": len(orders) == len(fills),
        "trade_count": len(orders),
        "risk": risk,
        "positions": {key: value.model_dump(mode="json") for key, value in account.positions.items()},
        "fills": fills,
        "operations": operations,
        "samples": samples,
    }
    output = store.path.parent / "rapports" / store.path.stem
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Refuse an output directory that other OS users could substitute files in.
    marker = output / ".private"
    private_database(marker)
    _atomic_write(output / "bilan.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    table = io.StringIO(newline="")
    writer = csv.writer(table)
    writer.writerow(["order_id", "timestamp", "symbol", "side", "quantity", "fill_price", "fee_eur"])
    for fill in fills:
        # Prefix spreadsheet formulas in any string field, including adapter-supplied symbols.
        values = [
            fill.get(key, "")
            for key in ("order_id", "timestamp", "symbol", "side", "quantity", "price", "fee")
        ]
        writer.writerow(
            [
                "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                for value in values
            ]
        )
    _atomic_write(output / "operations.csv", table.getvalue())
    rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value))}</td></tr>"
        for label, value in [
            ("Source des prix", report["market_source"]),
            ("Valorisation (EUR)", report["equity_eur"]),
            ("Cash (EUR)", report["cash_eur"]),
            ("Dépôts nets (EUR)", report["net_deposits_eur"]),
            ("PnL net (EUR)", report["net_pnl_eur"]),
            ("Frais enregistrés, déjà inclus au PnL (EUR)", report["recorded_fees_eur"]),
            (
                "Historique de frais complet",
                "Oui" if report["complete_fee_history"] else "Non — ancienne base",
            ),
            ("Ordres exécutés", report["trade_count"]),
            ("État", risk.get("reason") or "Simulation active / arrêt normal"),
            ("Liquidation confirmée", risk.get("liquidation_completed", False)),
        ]
    )
    document = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Bilan paper trading</title><style>body{{font:16px system-ui;max-width:900px;margin:3rem auto;padding:1rem;
background:#101722;color:#edf3fc}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:.8rem;
border-bottom:1px solid #354155}}p{{line-height:1.6}}th{{width:55%}}</style>
<h1>Bilan de simulation</h1><p>{html.escape(report["warning"])}</p>
<p>Édité le {html.escape(report["generated_at"])}. Valorisation au dernier prix sauvegardé.</p>
<table>{rows}</table><p>Un arrêt à 5 % déclenche une liquidation, sans garantir son prix d'exécution.
Ce bilan ne constitue pas une validation de stratégie sur des données réelles.</p></html>"""
    path = output / "bilan.html"
    _atomic_write(path, document)
    return path
