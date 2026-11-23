"""Transparent, rule-based risk scores (0-100). The AI never invents these numbers."""
import math
import statistics


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def percentile(values: list, p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def flood_tier(score: int) -> str:
    return "Low" if score < 25 else "Moderate" if score < 45 else "High" if score < 65 else "Severe"


def landslide_tier(score: int) -> str:
    return "Low" if score < 25 else "Moderate" if score < 45 else "High" if score < 65 else "Very High"


def discharge_stats(q: list, qmax: list) -> dict:
    """q / qmax hold 92 past days followed by 7 forecast days (None where the river grid has no data)."""
    fc = q[-7:]
    fmax = qmax[-7:] if qmax else [None] * 7
    hist = [v for v in q[:-7] if v is not None]
    fvals = [v for v in fc if v is not None]
    if not fvals or len(hist) < 10:
        return {"available": False}
    baseline = statistics.median(hist)
    p90 = percentile(hist, 0.90)
    threshold = max(p90, baseline * 1.25, 0.05)
    peak = max(fvals)
    peak_idx = fc.index(peak)
    surge = peak / max(baseline, 0.05)
    return {"available": True, "baseline": baseline, "p90": p90, "hist_days": len(hist), "threshold": threshold, "peak": peak,
            "peak_idx": peak_idx, "surge_ratio": surge,
            "ensemble_peak": max([v for v in fmax if v is not None] or [peak])}


def flood_score(dis: dict, rain_total: float, rain_max_day: float) -> dict:
    rain_c = clamp(rain_total / 200.0)
    day_c = clamp(rain_max_day / 80.0)
    if dis.get("available"):
        surge_c = 0.0 if dis["surge_ratio"] <= 1.2 else clamp(math.log(dis["surge_ratio"] / 1.2) / math.log(5))
        exceed_c = clamp((dis["peak"] / dis["threshold"] - 0.5) / 0.7)
        score = 100 * (0.35 * surge_c + 0.30 * exceed_c + 0.25 * rain_c + 0.10 * day_c)
        comps = {"surge": round(surge_c, 2), "threshold_exceedance": round(exceed_c, 2),
                 "rain_total": round(rain_c, 2), "rain_peak_day": round(day_c, 2)}
    else:
        score = 100 * (0.6 * rain_c + 0.4 * day_c)
        comps = {"rain_total": round(rain_c, 2), "rain_peak_day": round(day_c, 2)}
    s = int(round(clamp(score, 0, 100)))
    return {"score": s, "tier": flood_tier(s), "components": comps}


def landslide_score(terrain: dict, rain_7d: float, antecedent_3d: float, rain_max_day: float,
                    soil_peak, soil_mean) -> dict:
    relief_c = clamp(terrain["relief_m"] / 900.0)
    elev_c = clamp(terrain["elevation_m"] / 1500.0)
    soil_c = clamp(((soil_peak if soil_peak is not None else 0.2) - 0.15) / 0.30)
    rain_c = clamp((rain_7d + antecedent_3d) / 300.0)
    day_c = clamp(rain_max_day / 100.0)
    score = 100 * (0.32 * relief_c + 0.08 * elev_c + 0.25 * soil_c + 0.22 * rain_c + 0.13 * day_c)
    s = int(round(clamp(score, 0, 100)))
    return {"score": s, "tier": landslide_tier(s),
            "components": {"relief": round(relief_c, 2), "elevation": round(elev_c, 2),
                           "soil_saturation": round(soil_c, 2), "rain_trigger": round(rain_c, 2),
                           "rain_peak_day": round(day_c, 2)}}
