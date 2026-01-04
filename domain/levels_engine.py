from dataclasses import dataclass
import pandas as pd
@dataclass
class Level:
    ts: pd.Timestamp; kind: str; price: float
def previous_day_levels(d1: pd.DataFrame):
    last = d1.iloc[-2]
    return [Level(ts=last['ts'], kind='PDH', price=last['high']), Level(ts=last['ts'], kind='PDL', price=last['low'])]
