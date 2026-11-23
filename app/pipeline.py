"""The four-agent pipeline. Agents 1-3 gather and score live data; agent 4 is the Groq strategist."""
import json
import time
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from . import ai_groq, geo, scoring
from .errors import AppError

PROFILES = ["Town / Residential Ward", "Agricultural Farm & Crop Belt", "Hill / Ghat Road & Settlement"]
DEFAULT_PROFILE = PROFILES[0]

AGENTS = [
    (1, "Agent 1: Geospatial & Elevation Resolver"),
    (2, "Agent 2: River Discharge Hydrologist"),
    (3, "Agent 3: Terrain & Soil Saturation Assessor"),
    (4, "Agent 4: Groq Disaster Preparedness Strategist"),
]

HELPLINES = [
    {"name": "National Emergency Response", "number": "112"},
    {"name": "Ambulance", "number": "108"},
    {"name": "Disaster Management Helpline", "number": "1070"},
    {"name": "NDMA Control Room", "number": "1078"},
    {"name": "Kisan Call Centre (farm advice)", "number": "1800-180-1551"},
]
DISCLAIMER = ("Indicative model output built from open forecast data. It is not an official warning. "
              "Always follow alerts from IMD, CWC and your District Disaster Management Authority.")


class Advisory(BaseModel):
    headline: str = Field(min_length=3)
    flood_summary: str = Field(min_length=3)
    landslide_summary: str = Field(min_length=3)
    immediate_24h_actions: List[str] = Field(min_length=2)
    next_48_72h_actions: List[str] = Field(min_length=1)
    agri_livestock_steps: List[str] = Field(min_length=2)
    evacuation_checklist: List[str] = Field(min_length=3)
    confidence_note: str = ""


class Comparison(BaseModel):
    verdict: str = Field(min_length=3)
    higher_flood_risk: Literal["A", "B", "similar"]
    higher_landslide_risk: Literal["A", "B", "similar"]
    key_differences: List[str] = Field(min_length=2)
    recommendation: str = Field(min_length=3)


def clean_advisory(a: Advisory) -> dict:
    d = a.model_dump()
    for k in ("immediate_24h_actions", "next_48_72h_actions", "agri_livestock_steps", "evacuation_checklist"):
        d[k] = [str(x).strip() for x in d[k] if str(x).strip()][:8]
    return d


def validate_query(location, profile):
    loc = (location or "").strip() if isinstance(location, str) else ""
    if not loc or len(loc) > 120 or not any(ch.isalpha() for ch in loc):
        raise AppError(400, "BAD_INPUT", "Type an Indian town, district or farm location, for example \"Wayanad, Kerala\".")
    prof = profile if profile in PROFILES else DEFAULT_PROFILE
    return loc, prof


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_label(iso: str) -> str:
    return iso


async def gather(client, loc: str, profile: str, emit=None, check_ai=None):
    """Run agents 1-3. `emit` is an optional async callback(event_dict).
    `check_ai` runs right after the place is found, so a missing key fails fast."""
    agents = []

    async def start(n):
        if emit:
            await emit({"event": "agent", "step": n, "status": "running"})

    async def done(n, ms, summary):
        agents.append({"step": n, "name": dict(AGENTS)[n], "status": "done", "ms": ms, "summary": summary})
        if emit:
            await emit({"event": "agent", "step": n, "status": "done", "ms": ms, "summary": summary})

    # Agent 1
    await start(1)
    t = time.time()
    place = await geo.geocode(client, loc)
    if not place:
        raise AppError(400, "BAD_INPUT",
                       f"We couldn't find \"{loc}\" in India. Check the spelling or try a nearby town or district.")
    terrain = await geo.elevation_profile(client, place["latitude"], place["longitude"])
    t_geo = _utc_now()
    await done(1, int((time.time() - t) * 1000),
               f"{place['name']}, {place['admin1']} at {terrain['elevation_m']} m. {terrain['class']}, "
               f"{terrain['relief_m']} m relief in an 11 km window.")

    if check_ai:
        check_ai()

    # Agents 2 and 3 read independent APIs, but are shown in order.
    await start(2)
    t = time.time()
    flood_raw = await geo.river_discharge(client, place["latitude"], place["longitude"])
    t_flood = _utc_now()
    dis = scoring.discharge_stats(flood_raw["q"], flood_raw["qmax"])
    if dis["available"]:
        s2 = (f"Peak {dis['peak']:.1f} m³/s on {flood_raw['dates'][-7:][dis['peak_idx']]}, "
              f"{dis['surge_ratio']:.1f}x the recent median ({dis['baseline']:.1f} m³/s).")
    else:
        s2 = "No river channel found in the 5 km discharge grid cell. Flood score uses rainfall only."
    await done(2, int((time.time() - t) * 1000), s2)

    await start(3)
    t = time.time()
    wx = await geo.rain_and_soil(client, place["latitude"], place["longitude"])
    t_wx = _utc_now()
    await done(3, int((time.time() - t) * 1000), "Rainfall and soil moisture loaded.")

    # ---- assemble 7-day series --------------------------------------------------
    f_dates = wx["dates"][-7:]
    precip7 = [v or 0.0 for v in wx["precip"][-7:]]
    ante = sum(v or 0.0 for v in wx["precip"][:-7])
    fq = flood_raw["q"][-7:]
    fqmax = flood_raw["qmax"][-7:]
    series = []
    for i, d in enumerate(f_dates):
        sm = wx["soil"].get(d)
        series.append({
            "date": d,
            "discharge_m3s": round(fq[i], 2) if i < len(fq) and fq[i] is not None else None,
            "discharge_max_m3s": round(fqmax[i], 2) if i < len(fqmax) and fqmax[i] is not None else None,
            "precip_mm": round(precip7[i], 1),
            "precip_prob_pct": wx["prob"][-7:][i] if i < len(wx["prob"][-7:]) else None,
            "soil_moisture": round(sm, 3) if sm is not None else None})
    soils = [s["soil_moisture"] for s in series if s["soil_moisture"] is not None]
    soil_peak = max(soils) if soils else None
    soil_mean = sum(soils) / len(soils) if soils else None
    rain_total = sum(precip7)
    rain_max = max(precip7) if precip7 else 0.0

    fl = scoring.flood_score(dis, rain_total, rain_max)
    ls = scoring.landslide_score(terrain, rain_total, ante, rain_max, soil_peak, soil_mean)
    agents[1]["summary"] = s2 + f" Flood risk {fl['score']}/100 ({fl['tier']})."
    agents[2]["summary"] = (f"{rain_total:.0f} mm rain over 7 days (+{ante:.0f} mm in the last 3), "
                            f"soil moisture peak {soil_peak if soil_peak is not None else 'n/a'} m³/m³. "
                            f"Landslide score {ls['score']}/100 ({ls['tier']}).")

    return {
        "location": {"query": loc, "name": place["name"], "admin1": place["admin1"], "country": "India",
                     "latitude": round(place["latitude"], 4), "longitude": round(place["longitude"], 4),
                     "elevation_m": terrain["elevation_m"],
                     "terrain": {k: terrain[k] for k in ("class", "relief_m", "grid_min_m", "grid_max_m", "mean_slope_deg")}},
        "asset_profile": profile,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecast_period": {"start": f_dates[0] if f_dates else None, "end": f_dates[-1] if f_dates else None},
        "flood": {"score": fl["score"], "tier": fl["tier"], "components": fl["components"],
                  "discharge_available": dis["available"],
                  "peak_discharge_m3s": round(dis["peak"], 2) if dis["available"] else None,
                  "peak_date": f_dates[dis["peak_idx"]] if dis["available"] and dis["peak_idx"] < len(f_dates) else None,
                  "baseline_m3s": round(dis["baseline"], 2) if dis["available"] else None,
                  "surge_ratio": round(dis["surge_ratio"], 2) if dis["available"] else None,
                  "threshold_m3s": round(dis["threshold"], 2) if dis["available"] else None,
                  "threshold_basis": "Highest of: 90th percentile of the past 92 days, or 1.25 x the 92-day median"},
        "landslide": {"score": ls["score"], "tier": ls["tier"], "terrain_class": terrain["class"],
                      "components": ls["components"], "soil_moisture_peak": soil_peak,
                      "soil_moisture_mean": round(soil_mean, 3) if soil_mean is not None else None},
        "rain": {"total_7d_mm": round(rain_total, 1), "antecedent_3d_mm": round(ante, 1),
                 "max_daily_mm": round(rain_max, 1)},
        "series": series,
        "telemetry": {
            "latitude": round(place["latitude"], 4), "longitude": round(place["longitude"], 4),
            "dem_elevation_m": terrain["elevation_m"],
            "p90_threshold_m3s": round(dis["p90"], 2) if dis["available"] else None,
            "median_baseline_m3s": round(dis["baseline"], 2) if dis["available"] else None,
            "applied_threshold_m3s": round(dis["threshold"], 2) if dis["available"] else None,
            "history_days_used": dis.get("hist_days") if dis["available"] else 0,
            "sources": [
                {"name": "Open-Meteo Geocoding + Elevation (DEM)", "fetched_at": t_geo,
                 "data_window": "static 3x3 DEM grid, 0.05 deg spacing"},
                {"name": "Open-Meteo Flood API (river discharge)", "fetched_at": t_flood,
                 "data_window": f"{flood_raw['dates'][0]} to {flood_raw['dates'][-1]}" if flood_raw["dates"] else None},
                {"name": "Open-Meteo Forecast API (rain, soil moisture)", "fetched_at": t_wx,
                 "data_window": f"{wx['dates'][0]} to {wx['dates'][-1]}" if wx["dates"] else None},
            ]},
        "helplines": HELPLINES,
        "agents": agents,
        "disclaimer": DISCLAIMER,
    }


ADVISORY_SYSTEM = (
    "You are a disaster-preparedness strategist for Indian towns, farms and hill settlements. "
    "You receive forecast telemetry and risk scores that the system already computed. "
    "Never invent measurements; quote only numbers that appear in the data. Tailor advice to the asset profile. "
    "Use short, plain sentences a village sarpanch or farmer can act on. This is advice, not an official warning. "
    "Reply with ONLY one JSON object with exactly these keys: "
    "headline (string), flood_summary (string), landslide_summary (string), "
    "immediate_24h_actions (array of 3-6 strings), next_48_72h_actions (array of 2-5 strings), "
    "agri_livestock_steps (array of 3-6 strings; for non-farm profiles give livestock, stored-food and household "
    "asset protection steps), evacuation_checklist (array of 4-8 strings), confidence_note (string)."
)


def _telemetry_for_prompt(r: dict) -> str:
    slim = {k: r[k] for k in ("location", "asset_profile", "flood", "landslide", "rain", "series", "forecast_period")}
    return json.dumps(slim, separators=(",", ":"))


async def strategist(r: dict, emit=None):
    if emit:
        await emit({"event": "agent", "step": 4, "status": "running"})
    t = time.time()
    adv, model = await ai_groq.chat_json(
        ADVISORY_SYSTEM,
        "Write the 72-hour preparedness plan for this location.\nTELEMETRY:\n" + _telemetry_for_prompt(r),
        Advisory)
    ms = int((time.time() - t) * 1000)
    summary = f"72-hour plan written by {model}."
    r["advisory"] = clean_advisory(adv)
    r["advisory_source"] = "groq"
    r["model"] = model
    r["agents"].append({"step": 4, "name": dict(AGENTS)[4], "status": "done", "ms": ms, "summary": summary})
    if emit:
        await emit({"event": "agent", "step": 4, "status": "done", "ms": ms, "summary": summary})
    return r


COMPARE_SYSTEM = (
    "You compare two Indian locations (A and B) using forecast telemetry and risk scores already computed by the system. "
    "Never invent numbers. Reply with ONLY one JSON object with keys: verdict (string, 1-2 sentences), "
    "higher_flood_risk (\"A\", \"B\" or \"similar\"), higher_landslide_risk (\"A\", \"B\" or \"similar\"), "
    "key_differences (array of 3-5 short strings), recommendation (string, what the authorities or residents "
    "of the higher-risk place should do first)."
)


async def compare_strategist(a: dict, b: dict):
    def brief(r):
        return {"name": f"{r['location']['name']}, {r['location']['admin1']}", "asset_profile": r["asset_profile"],
                "elevation_m": r["location"]["elevation_m"], "terrain": r["location"]["terrain"]["class"],
                "flood": {k: r["flood"][k] for k in ("score", "tier", "peak_discharge_m3s", "surge_ratio")},
                "landslide": {k: r["landslide"][k] for k in ("score", "tier", "soil_moisture_peak")},
                "rain": r["rain"]}
    prompt = "A:\n" + json.dumps(brief(a), separators=(",", ":")) + "\nB:\n" + json.dumps(brief(b), separators=(",", ":"))
    cmp_, model = await ai_groq.chat_json(COMPARE_SYSTEM, prompt, Comparison, max_tokens=2000)
    d = cmp_.model_dump()
    d["key_differences"] = [str(x).strip() for x in d["key_differences"] if str(x).strip()][:6]
    return d, model
