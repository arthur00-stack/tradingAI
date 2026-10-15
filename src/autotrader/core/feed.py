"""Dedicated ingestion thread. Latest-value mailbox bounds memory and latency."""

import asyncio
import json
import logging
import random
import time
from threading import Event, Lock

from autotrader.core.types import D, Tick

log = logging.getLogger(__name__)


class Mailbox:
    def __init__(self) -> None:
        self.lock = Lock()
        self.latest: Tick | None = None

    def put(self, tick: Tick) -> None:
        with self.lock:
            if self.latest is None or tick.timestamp > self.latest.timestamp:
                self.latest = tick

    def get(self) -> Tick | None:
        with self.lock:
            return self.latest


def paper_feed(mailbox: Mailbox, stop: Event, initial: float = 100.0) -> None:
    """Synthetic market only; no broker credentials or external side effects."""
    rng = random.Random()
    price = initial
    while not stop.is_set():
        price *= 1 + rng.gauss(0, 0.0005)
        mailbox.put(Tick(symbol="DEMO/EUR", price=D(str(price)), timestamp=time.time()))
        stop.wait(0.5)


async def websocket_feed(url: str, mailbox: Mailbox, stop: Event) -> None:
    """Normalized vendor gateway: {symbol, price:string, timestamp:unix-seconds}.

    Vendor-specific subscription/normalization belongs in an adapter. This
    generic source reconnects with bounded backoff; stale quotes are rejected
    by the engine. Network receive has a timeout so shutdown can progress.
    """
    from websockets.asyncio.client import connect

    backoff = 1.0
    while not stop.is_set():
        try:
            async with connect(url, open_timeout=5, close_timeout=2, ping_timeout=5) as ws:
                backoff = 1.0
                while not stop.is_set():
                    try:
                        payload = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except TimeoutError:
                        continue
                    data = json.loads(payload)
                    mailbox.put(
                        Tick(
                            symbol=data["symbol"], price=D(data["price"]), timestamp=float(data["timestamp"])
                        )
                    )
        except Exception:
            log.exception("WebSocket disconnected")
            for _ in range(int(backoff * 10)):
                if stop.is_set():
                    return
                await asyncio.sleep(0.1)
            backoff = min(backoff * 2, 30.0)
