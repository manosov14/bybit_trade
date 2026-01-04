from typing import Dict, Any
from infra.env import load_env
from domain.levels import LevelsParams, working_levels_d1, working_levels_d1_for_retro

def get_pd_levels(d1_df, side: str, env: Dict[str, Any], for_retro: bool=False):
    days = int(env.get('DAYS', 10) or 10)
    include_inside = str(env.get('INCLUDE_INSIDE', 'true')).lower() in ('1','true','yes','y')
    params = LevelsParams(days_window=days, include_inside=include_inside)
    if for_retro:
        return working_levels_d1_for_retro(d1_df, side, params=params)
    return working_levels_d1(d1_df, side, params=params)

def decide_for_retro(env: Dict[str, Any]) -> bool:
    v = str(env.get('LEVELS_INCLUDE_TODAY_FOR_RETRO', 'true')).lower()
    return v in ('1','true','yes','y')
