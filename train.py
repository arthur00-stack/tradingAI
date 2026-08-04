import sys

from fetch_data import load_data
from trading_env import StockTradingEnv

try:
    from stable_baselines3 import PPO
except ImportError:
    PPO = None

try:
    from gymnasium.wrappers import GymnasiumCompatibilityV0
except ImportError:
    GymnasiumCompatibilityV0 = None


def main():
    if PPO is None:
        raise ImportError(
            "Le paquet 'stable-baselines3' n'est pas installé. "
            "Installez-le avec : pip install stable-baselines3"
        )

    df = load_data("AAPL", start="2020-01-01", end="2023-01-01")
    env = StockTradingEnv(df)

    if GymnasiumCompatibilityV0 is not None:
        env = GymnasiumCompatibilityV0(env)

    model = PPO("MlpPolicy", env, verbose=1, learning_rate=0.0003)

    print("Début de l'entraînement...")
    model.learn(total_timesteps=50000)

    model.save("ppo_trading_aapl")
    print("Modèle entraîné et sauvegardé avec succès !")


if __name__ == "__main__":
    main()
