"""Isolated inference process: no broker reference, credentials or execution rights."""

import logging
from pathlib import Path
from typing import Any


def inference_worker(requests: Any, responses: Any, checkpoint: str | None) -> None:
    import torch

    from autotrader.ml.model import load_policy

    torch.set_num_threads(1)
    try:
        model = load_policy(Path(checkpoint)) if checkpoint else None
        while True:
            request = requests.get()
            if request is None:
                return
            identifier, observation, memory = request
            if model is None:
                responses.put((identifier, 0, None, None))
                continue
            hidden = None if memory is None else tuple(torch.tensor(x, dtype=torch.float32) for x in memory)
            with torch.inference_mode():
                logits, _, hidden = model(torch.tensor(observation).reshape(1, 1, 3), hidden)
            if not torch.isfinite(logits).all() or any(not torch.isfinite(h).all() for h in hidden):
                raise ValueError("Non-finite policy output")
            action = int(logits[0, 0].argmax().item())
            responses.put((identifier, action, [h.tolist() for h in hidden], None))
    except Exception as error:
        logging.getLogger(__name__).exception("Inference worker failed")
        responses.put((-1, 0, None, str(error)))
