"""Exercise the actual Qt/threads/spawn lifecycle without a display server."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("demo", [False, True])
def test_start_reconcile_stop_and_restart(tmp_path: Path, demo: bool) -> None:
    database = tmp_path / "paper.sqlite3"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    extra = []
    if demo:
        import torch

        from autotrader.ml.model import SCHEMA, ActorCritic

        checkpoint = tmp_path / "test-model.pt"
        torch.save({"schema": SCHEMA, "weights": ActorCritic().state_dict()}, checkpoint)
        extra = ["--demo", "--checkpoint", str(checkpoint)]
    for _ in range(2):
        process = subprocess.Popen(
            [sys.executable, "-m", "autotrader.main", "--database", str(database), *extra],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 20
            recovered = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(process.communicate())
                if database.exists():
                    try:
                        with sqlite3.connect(database) as db:
                            row = db.execute("SELECT value FROM state WHERE key='engine'").fetchone()
                        if row:
                            state = json.loads(row[0])
                            if demo:
                                if state["account"]["net_deposits"] != "10":
                                    time.sleep(0.1)
                                    continue
                                assert state["model_id"] != "hold-only"
                            else:
                                assert state["model_id"] == "hold-only"
                            recovered = True
                            break
                    except sqlite3.OperationalError:
                        pass
                time.sleep(0.1)
            assert recovered
            time.sleep(0.5)
            process.terminate()  # SIGTERM must take the graceful Qt close path.
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, (stdout, stderr)
            assert "Traceback" not in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
