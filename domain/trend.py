from .bias import d1_bias, h4_bias_ma

def d1_trend(d1_df, sma_len: int):
    """Return 'LONG' or 'SHORT' for D1 timeframe by SMA."""
    return d1_bias(d1_df, sma_len)

def h4_trend(h4_df, sma_len: int):
    """Return 'LONG' or 'SHORT' for H4 timeframe by SMA."""
    return h4_bias_ma(h4_df, sma_len)

def combined_trend(d1_df, h4_df, d1_len:int, h4_len:int, use_d1:bool=True, use_h4:bool=True):
    """
    Combine D1/H4 trends per flags.
    If both true and equal -> that direction; if conflict -> prefer the one with its flag True.
    """
    d1 = d1_trend(d1_df, d1_len) if use_d1 else None
    h4 = h4_trend(h4_df, h4_len) if use_h4 else None
    if d1 and h4:
        return d1 if d1==h4 else (d1 if use_d1 and not use_h4 else (h4 if use_h4 and not use_d1 else d1))
    return d1 or h4 or None
