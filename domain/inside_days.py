
import pandas as pd

def count_inside_days(d1: pd.DataFrame, upto_index: int=-2)->int:
    """
    Считает число подряд идущих внутренних дней до upto_index (не включая текущий последний день).
    Внутренний день: high<=high_prev и low>=low_prev.
    """
    df = d1.copy().reset_index(drop=True)
    if len(df)<3: return 0
    i = len(df)+upto_index
    cnt=0
    while i-1>=0:
        hi, lo = df.loc[i,'high'], df.loc[i,'low']
        hip, lop = df.loc[i-1,'high'], df.loc[i-1,'low']
        if hi<=hip and lo>=lop:
            cnt+=1; i-=1
        else:
            break
    return cnt
