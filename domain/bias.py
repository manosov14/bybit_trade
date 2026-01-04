from indicators.ta_utils import sma

def d1_bias(d1, n):
    d1=d1.copy(); d1['sma']=sma(d1['close'], n)
    return 'LONG' if d1.iloc[-1]['close'] > d1.iloc[-1]['sma'] else 'SHORT'

def h4_bias_ma(h4, n):
    h4=h4.copy(); h4['sma']=sma(h4['close'], n)
    return 'LONG' if h4.iloc[-1]['close'] > h4.iloc[-1]['sma'] else 'SHORT'

def combined_bias_flags(d1, h4, use_d1: bool, use_h4: bool, d1_len: int, h4_len: int, priority: str):
    b_d1 = d1_bias(d1, d1_len) if use_d1 else 'NONE'
    b_h4 = h4_bias_ma(h4, h4_len) if use_h4 else 'NONE'
    if use_d1 and not use_h4:
        b_eff = b_d1
    elif use_h4 and not use_d1:
        b_eff = b_h4
    elif use_d1 and use_h4:
        if b_d1 == 'NONE' and b_h4 != 'NONE':
            b_eff = b_h4
        elif b_h4 == 'NONE' and b_d1 != 'NONE':
            b_eff = b_d1
        elif b_d1 == b_h4:
            b_eff = b_d1
        else:
            b_eff = b_h4 if priority.upper()=='H4' else b_d1
    else:
        b_eff = 'NONE'
    return b_d1, b_h4, b_eff
