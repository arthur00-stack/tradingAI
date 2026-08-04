# Trading IA - Learn Python

Application de simulation de trading boursier avec une interface graphique, un environnement Gym, et un agent LSTM Actor-Critic.

## Contenu du projet

- `fetch_data.py` : téléchargement de données financières et calcul des indicateurs techniques (SMA, RSI).
- `trading_env.py` : environnement de trading compatible Gym/Gymnasium.
- `train_lstm.py` : modèle LSTM Actor-Critic et routine d'entraînement.
- `train.py` : exemple d'entraînement PPO avec Stable Baselines 3.
- `gui_app.py` : tableau de bord PyQt6 pour lancer une simulation et visualiser les résultats.

## Prérequis

- Python 3.10+
- Packages Python :
  - `pandas`
  - `yfinance` ou `pandas_datareader`
  - `PyQt6`
  - `pyqtgraph`
  - `torch`
  - `gymnasium`
  - `stable-baselines3` (optionnel pour `train.py`)

## Installation

1. Créez un environnement virtuel :

```bash
python -m venv venv
```

2. Activez l'environnement :

```powershell
venv\Scripts\Activate.ps1
```

3. Installez les dépendances :

```bash
pip install pandas pyqt6 pyqtgraph torch gymnasium yfinance pandas_datareader
```

- Si vous souhaitez entraîner avec PPO :

```bash
pip install stable-baselines3
```

## Utilisation

- Lancer l'application graphique :

```bash
python gui_app.py
```

- Entraîner l'agent LSTM :

```bash
python train_lstm.py
```

- Entraîner un agent PPO :

```bash
python train.py
```

## Notes

- `fetch_data.py` propose un alias `load_financial_data` pour compatibilité.
- Si `yfinance` n'est pas installé, le projet utilise `pandas_datareader` en secours.
- Les fichiers de simulation générés peuvent être exportés sous forme de CSV.

## Déploiement GitHub

1. Initialisez un dépôt Git local :

```bash
git init
```

2. Ajoutez une télécommande distante GitHub :

```bash
git remote add origin https://github.com/<votre-utilisateur>/<votre-repo>.git
```

3. Poussez sur la branche principale :

```bash
git push -u origin main
```
