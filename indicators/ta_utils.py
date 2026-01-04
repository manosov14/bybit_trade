import pandas as pd, numpy as np
def sma(s: pd.Series, n:int)->pd.Series: return s.rolling(n, min_periods=n).mean()
def atr(df: pd.DataFrame, n:int)->pd.Series:
    h,l,c = df['high'],df['low'],df['close']
    tr = np.maximum(h-l, np.maximum((h-c.shift()).abs(), (l-c.shift()).abs()))
    return tr.rolling(n, min_periods=n).mean()
def zscore(series: pd.Series, lookback:int)->pd.Series:
    mu = series.rolling(lookback, min_periods=lookback).mean()
    sd = series.rolling(lookback, min_periods=lookback).std(ddof=0)
    return (series-mu)/sd
