"""Offline demonstration preparation; not a profitable or validated strategy."""

from pathlib import Path


def prepare_demo(directory: Path) -> Path:
    checkpoint = directory / "policy.pt"
    if checkpoint.exists():
        return checkpoint
    import numpy as np

    from autotrader.ml.train import train

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    rng = np.random.default_rng(42)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 256)))
    np.save(directory / "prices.npy", prices)
    print("Préparation de la démonstration sur données fictives…", flush=True)
    train(prices, checkpoint, episodes=20)
    (directory / "NOTICE.txt").write_text(
        "Démonstration sur données synthétiques. Aucun gain garanti. Ne pas utiliser en réel.\n"
    )
    return checkpoint
