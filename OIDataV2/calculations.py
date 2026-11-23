"""Pure calculations; missing values never become fabricated zero observations."""
import math


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def difference(a, b):
    a, b = number(a), number(b)
    return None if a is None or b is None else a - b


def money(oi, previous_oi, atp, multiplier=1):
    change, price = difference(oi, previous_oi), number(atp)
    if change is None or price is None or price <= 0:
        return None
    return change * price * multiplier / 100000


def interpretation(kind, price_change, oi_change):
    p, o = number(price_change), number(oi_change)
    if p is None or o is None or p == 0 or o == 0:
        return "—", "—"
    term = { (True, True): "Long buildup", (False, True): "Short buildup",
             (True, False): "Short covering", (False, False): "Long unwinding" }[p > 0, o > 0]
    if kind == "FUT":
        bullish, aggressive = p > 0, o > 0
    else:
        bullish = (p > 0) if kind == "CE" else (p < 0)
        aggressive = (p > 0) != (o > 0)
    return term, ("Aggressive " if aggressive else "") + ("Bullish" if bullish else "Bearish")


def net_view(views):
    if len(views) != 3 or any(v == "—" for v in views):
        return "Incomplete"
    for side in ("Bullish", "Bearish"):
        if all(side in v for v in views):
            return ("Strong " if sum("Aggressive" in v for v in views) >= 2 else "") + side
    return "Mixed"


def bias(ce, pe, threshold=20):
    if ce is None or pe is None:
        return {"view": "Incomplete", "difference_pct": None}
    base = max(abs(ce), abs(pe))
    pct = abs(pe - ce) / base * 100 if base else 0
    # Negative OI changes are unwinding, not evidence of fresh writing.
    view = "Unwinding / mixed" if min(ce, pe) < 0 else (
        "Neutral" if pct < threshold or ce == pe else ("Bullish" if pe > ce else "Bearish"))
    return {"view": view, "difference_pct": pct}
