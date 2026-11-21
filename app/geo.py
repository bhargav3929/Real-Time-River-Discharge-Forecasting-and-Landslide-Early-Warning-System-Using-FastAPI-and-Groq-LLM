"""Open-Meteo clients: geocoding, elevation (DEM), river discharge, precipitation and soil moisture."""
import asyncio
import math
from collections import defaultdict

import httpx

from .config import S
from .errors import AppError

# Fallback for hill districts that the geocoder does not list as a place name.
GAZETTEER = {
    "wayanad": ("Wayanad", "Kerala", 11.6103, 76.0830),
    "kalpetta": ("Kalpetta", "Kerala", 11.6103, 76.0830),
    "munnar": ("Munnar", "Kerala", 10.0889, 77.0595),
    "idukki": ("Idukki", "Kerala", 9.8494, 76.9710),
    "kodagu": ("Kodagu", "Karnataka", 12.4244, 75.7382),
    "coorg": ("Kodagu", "Karnataka", 12.4244, 75.7382),
    "madikeri": ("Madikeri", "Karnataka", 12.4244, 75.7382),
    "nilgiris": ("Nilgiris", "Tamil Nadu", 11.4102, 76.6950),
    "darjeeling": ("Darjeeling", "West Bengal", 27.0360, 88.2627),
    "kalimpong": ("Kalimpong", "West Bengal", 27.0594, 88.4695),
    "gangtok": ("Gangtok", "Sikkim", 27.3389, 88.6065),
    "uttarkashi": ("Uttarkashi", "Uttarakhand", 30.7268, 78.4354),
    "chamoli": ("Chamoli", "Uttarakhand", 30.4000, 79.3200),
    "joshimath": ("Joshimath", "Uttarakhand", 30.5550, 79.5650),
    "kullu": ("Kullu", "Himachal Pradesh", 31.9592, 77.1089),
    "shimla": ("Shimla", "Himachal Pradesh", 31.1048, 77.1734),
    "kohima": ("Kohima", "Nagaland", 25.6751, 94.1086),
    "ratnagiri": ("Ratnagiri", "Maharashtra", 16.9902, 73.3120),
}

_HEADERS = {"User-Agent": "flood-landslide-risk/1.0 (local student project)"}


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            last = r.status_code
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = type(e).__name__
        await asyncio.sleep(1.0 + attempt)
    raise AppError(502, "DATA_UNAVAILABLE",
                   "The live weather and river data service didn't answer. Check your internet connection and try again.")


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20.0, headers=_HEADERS)


# ---- Agent 1: geocoding + elevation --------------------------------------
async def geocode(client: httpx.AsyncClient, query: str):
    """Return {name, admin1, latitude, longitude} for an Indian place, or None."""
    parts = [p.strip() for p in query.split(",") if p.strip()]
    candidates = [query.strip()] + parts[:1]
    seen = set()
    for cand in candidates:
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        data = await _get_json(client, S.GEOCODING_URL,
                               {"name": cand, "count": 10, "language": "en", "format": "json"})
        hits = [r for r in (data.get("results") or []) if r.get("country_code") == "IN"]
        if hits:
            hint = parts[1].lower() if len(parts) > 1 else ""
            pick = next((h for h in hits if hint and hint in (h.get("admin1") or "").lower()), hits[0])
            return {"name": pick.get("name"), "admin1": pick.get("admin1") or "India",
                    "latitude": pick["latitude"], "longitude": pick["longitude"]}
    for tok in [parts[0].lower()] if parts else []:
        if tok in GAZETTEER:
            n, a, lat, lon = GAZETTEER[tok]
            return {"name": n, "admin1": a, "latitude": lat, "longitude": lon}
    return None


def _terrain_class(relief: float) -> str:
    if relief < 60:
        return "Flat plain / floodplain"
    if relief < 200:
        return "Gently undulating terrain"
    if relief < 500:
        return "Hilly upland"
    if relief < 900:
        return "Steep hill terrain"
    return "Mountainous / ghat escarpment"


async def elevation_profile(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    """Centre elevation plus a 3x3 DEM grid (about 11 km across) to estimate relief and gradient."""
    step = 0.05
    pts = [(lat + dy * step, lon + dx * step) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    data = await _get_json(client, S.ELEVATION_URL, {
        "latitude": ",".join(f"{p[0]:.4f}" for p in pts),
        "longitude": ",".join(f"{p[1]:.4f}" for p in pts)})
    elev = data.get("elevation") or []
    if len(elev) != 9 or any(e is None for e in elev):
        raise AppError(502, "DATA_UNAVAILABLE", "Terrain elevation data was not available for this place. Try again.")
    centre = elev[4]
    relief = max(elev) - min(elev)
    angles = []
    for i, (plat, plon) in enumerate(pts):
        if i == 4:
            continue
        dy = (plat - lat) * 111_000
        dx = (plon - lon) * 111_000 * math.cos(math.radians(lat))
        angles.append(math.degrees(math.atan2(abs(elev[i] - centre), math.hypot(dx, dy))))
    return {"elevation_m": round(centre), "relief_m": round(relief), "grid_min_m": round(min(elev)),
            "grid_max_m": round(max(elev)), "mean_slope_deg": round(sum(angles) / len(angles), 1),
            "class": _terrain_class(relief)}


# ---- Agent 2: river discharge --------------------------------------------
def _num(v):
    return v if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else None


async def river_discharge(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    data = await _get_json(client, S.FLOOD_URL, {
        "latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata",
        "daily": "river_discharge,river_discharge_max", "past_days": 92, "forecast_days": 7})
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    q = [_num(v) for v in daily.get("river_discharge") or []]
    qmax = [_num(v) for v in daily.get("river_discharge_max") or []]
    return {"dates": dates, "q": q, "qmax": qmax}


# ---- Agent 3: precipitation and soil moisture ----------------------------
async def rain_and_soil(client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    data = await _get_json(client, S.FORECAST_URL, {
        "latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata", "past_days": 3, "forecast_days": 7,
        "daily": "precipitation_sum,precipitation_probability_max",
        "hourly": "soil_moisture_0_to_7cm,soil_moisture_7_to_28cm"})
    daily = data.get("daily") or {}
    soil = defaultdict(list)
    hourly = data.get("hourly") or {}
    for i, t in enumerate(hourly.get("time") or []):
        a = _num((hourly.get("soil_moisture_0_to_7cm") or [None] * (i + 1))[i])
        b = _num((hourly.get("soil_moisture_7_to_28cm") or [None] * (i + 1))[i])
        vals = [v for v in (a, b) if v is not None]
        if vals:
            soil[t[:10]].append(sum(vals) / len(vals))
    return {"dates": daily.get("time") or [],
            "precip": [_num(v) for v in daily.get("precipitation_sum") or []],
            "prob": [_num(v) for v in daily.get("precipitation_probability_max") or []],
            "soil": {d: sum(v) / len(v) for d, v in soil.items()}}
