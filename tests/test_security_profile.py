import os
import stat
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from autotrader.core.profile import TradingProfile
from autotrader.core.risk import RiskManager
from autotrader.core.security import private_file
from autotrader.core.store import Store
from autotrader.core.types import Account, D


@pytest.mark.parametrize(
    "key,value",
    [
        ("live_enabled", True),
        ("transfers_enabled", True),
        ("api_read_only", False),
        ("short_allowed", True),
        ("requested_leverage", 50),
        ("api_host", "0.0.0.0"),
    ],
)
def test_profile_refuses_unsafe_configuration(key, value):
    with pytest.raises(ValidationError):
        TradingProfile(**{key: value})


def test_database_private_permissions(tmp_path):
    path = tmp_path / "db.sqlite3"
    path.touch(mode=0o666)
    Store(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_database_symlink_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve")
    link = tmp_path / "db.sqlite3"
    link.symlink_to(target)
    with pytest.raises(OSError):
        Store(link)
    assert target.read_text() == "preserve"


def test_sidecar_symlink_rejected(tmp_path):
    target = tmp_path / "target"
    target.touch()
    (tmp_path / "db.sqlite3-wal").symlink_to(target)
    with pytest.raises(OSError):
        Store(tmp_path / "db.sqlite3")


def test_special_file_rejected(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(PermissionError):
        private_file(fifo)


def test_one_euro_limit_is_cumulative_and_survives_restart(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite3")
    panic = Event()
    risk = RiskManager(store, panic, max_total_loss_eur=D(1))
    risk.observe(Account(cash=D(10), net_deposits=D(10)))
    risk.observe(Account(cash=D("9.51"), net_deposits=D(10)))
    assert not panic.is_set()
    risk.observe(Account(cash=D(9), net_deposits=D(10)))
    assert panic.is_set() and risk.state.suspended
    restored = RiskManager(store, Event(), max_total_loss_eur=D(1))
    assert restored.state.suspended
    # A deposit does not replenish the loss allowance.
    restored.observe(Account(cash=D(19), net_deposits=D(20)))
    assert restored.panic.is_set()


def test_loss_budget_cannot_exceed_capital():
    with pytest.raises(ValidationError):
        TradingProfile(capital_eur=D(10), max_total_loss_eur=D(11))


def test_shared_state_directory_rejected(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    shared.chmod(0o777)
    with pytest.raises(PermissionError):
        Store(shared / "db.sqlite3")
