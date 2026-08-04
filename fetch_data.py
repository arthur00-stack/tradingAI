import pandas as pd


def compute_rsi(data, window=14):
    """Calcule l'indicateur RSI (Relative Strength Index)."""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()

    rs = gain / (loss + 1e-8)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def load_data(ticker="AAPL", start="2020-01-01", end="2024-01-01"):
    """Télécharge les données boursières et calcule les indicateurs techniques."""
    print(f"Téléchargement des données pour {ticker}...")
    try:
        import yfinance as yf

        df = yf.download(ticker, start=start, end=end)
    except ModuleNotFoundError:
        try:
            from pandas_datareader import data as pdr

            df = pdr.DataReader(ticker, "yahoo", start, end)
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "Les paquets 'yfinance' ou 'pandas_datareader' sont manquants. "
                "Installez-les avec : pip install yfinance websockets pandas_datareader"
            ) from exc

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

    df["SMA_10"] = df["Close"].rolling(window=10).mean()
    df["SMA_30"] = df["Close"].rolling(window=30).mean()
    df["RSI"] = compute_rsi(df["Close"], window=14)

    df = df.dropna().reset_index(drop=True)
    print(f"Données prêtes : {len(df)} bougies téléchargées.")
    return df


load_financial_data = load_data


if __name__ == "__main__":
    df = load_data()
    print(df.head())
