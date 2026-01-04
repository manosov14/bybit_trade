
from datetime import datetime
from typing import List

# Сессии в UTC по умолчанию (можно настроить в .env):
# EU: 07:00-16:00, US: 13:30-20:00, ASIA: 00:00-08:00
DEFAULT = {
    'EU':   (7, 16),
    'US':   (13, 21),
    'ASIA': (0, 8),
}

def is_session_allowed(now_utc: datetime, allowed: List[str], custom=None)->bool:
    sched = dict(DEFAULT); 
    if custom: sched.update(custom)
    h = now_utc.hour
    for k in allowed:
        if k not in sched: continue
        a,b = sched[k]
        # учтём возможный переход через полночь
        if a<=b and (h>=a and h<b):
            return True
        if a>b and (h>=a or h<b):
            return True
    return False
