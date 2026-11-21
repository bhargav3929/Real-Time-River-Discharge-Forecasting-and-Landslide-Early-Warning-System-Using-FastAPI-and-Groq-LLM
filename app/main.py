"""FastAPI app: standard LastSem routes plus /api/analyze and /api/compare."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, geo, keys, pipeline
from .config import S
from .errors import AppError
from .access import verify_ticket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

TICKET_MESSAGES = {
    "TICKET_REQUIRED": (401, "Sign in on lastsem.com to try your own input. The bundled samples work without it."),
    "TICKET_INVALID": (401, "This demo ticket isn't valid. Go back to lastsem.com and start the demo again."),
    "TICKET_EXPIRED": (401, "This demo ticket has expired. Go back to lastsem.com and start the demo again."),
    "TICKET_USED_UP": (429, "You have used all the live runs on this demo ticket. The bundled samples still work."),
    "DAILY_CAP_REACHED": (429, "The shared demo has reached today's limit. Try again tomorrow or open a bundled sample."),
}
RATE_MSG = "You have used all 15 free scans for this hour. Try again later, or open a bundled sample."


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    if S.DEMO_MODE:
        db.purge_demo()
        if not S.DEMO_TICKET_SECRET:
            log.warning("DEMO_MODE is on but DEMO_TICKET_SECRET is empty: every ticket will be rejected.")
    yield


app = FastAPI(title=S.PROJECT_NAME, version=S.VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- errors
@app.exception_handler(AppError)
async def _app_error(_r, e: AppError):
    return JSONResponse(e.body(), status_code=e.status)


@app.exception_handler(RequestValidationError)
async def _validation(_r, _e):
    return JSONResponse({"error": "The request wasn't in the expected format.", "code": "BAD_INPUT"}, status_code=400)


@app.exception_handler(StarletteHTTPException)
async def _http(_r, e: StarletteHTTPException):
    if e.status_code == 404:
        return JSONResponse({"error": "That page or item doesn't exist.", "code": "NOT_FOUND"}, status_code=404)
    return JSONResponse({"error": "That request isn't allowed here.", "code": "BAD_INPUT"}, status_code=e.status_code)


@app.exception_handler(Exception)
async def _unexpected(_r, e: Exception):
    log.exception("Unhandled error: %s", type(e).__name__)
    return JSONResponse({"error": "Something went wrong on the server. Please try again.", "code": "AI_FAILED"},
                        status_code=500)


# ---------------------------------------------------------------- helpers
def client_ip(request: Request) -> str:
    if S.TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff.strip():
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def peek_owner(request: Request):
    """Owner id for read-only views. Returns (owner or None, ticket payload or None)."""
    if not S.DEMO_MODE:
        return "local", None
    payload, err = verify_ticket(request.headers.get("x-lastsem-ticket"), S.DEMO_TICKET_SECRET, S.PROJECT_SLUG)
    return (payload["tid"], payload) if not err else (None, None)


def gate(request: Request):
    """Gate for live runs: returns (owner, ticket payload). Raises AppError when not allowed."""
    if not S.DEMO_MODE:
        return "local", None
    payload, err = verify_ticket(request.headers.get("x-lastsem-ticket"), S.DEMO_TICKET_SECRET, S.PROJECT_SLUG)
    if err:
        status, msg = TICKET_MESSAGES[err]
        raise AppError(status, err, msg)
    tid = payload["tid"]
    if db.demo_runs_used(tid) >= int(payload.get("runs", 0)):
        status, msg = TICKET_MESSAGES["TICKET_USED_UP"]
        raise AppError(status, "TICKET_USED_UP", msg)
    if db.demo_daily_count() >= S.DEMO_DAILY_CAP:
        status, msg = TICKET_MESSAGES["DAILY_CAP_REACHED"]
        raise AppError(status, "DAILY_CAP_REACHED", msg)
    return tid, payload


def check_ai_ready():
    key, _ = keys.resolve_key()
    if not key:
        from .ai_groq import MSG_NOT_CONFIGURED
        raise AppError(503, "AI_NOT_CONFIGURED", MSG_NOT_CONFIGURED)


async def read_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise AppError(400, "BAD_INPUT", "Send a JSON body with a location, for example {\"location\": \"Wayanad, Kerala\"}.")
    if not isinstance(body, dict):
        raise AppError(400, "BAD_INPUT", "Send a JSON object with a location.")
    return body


def keys_guard(request: Request):
    if S.DEMO_MODE:
        raise AppError(404, "NOT_FOUND", "That page or item doesn't exist.")
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or \
            "x-forwarded-for" in request.headers or "forwarded" in request.headers:
        raise AppError(403, "KEYS_LOCAL_ONLY", "API keys can only be changed from the same computer the app runs on.")


def sample_meta() -> list:
    out = []
    for p in sorted(S.SAMPLES_DIR.glob("*.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
            if m.get("id") and m.get("title"):
                out.append(m)
        except (OSError, ValueError):
            continue
    order = {"wayanad-kerala": 0, "kaziranga-assam": 1, "hyderabad-telangana": 2}
    return sorted(out, key=lambda m: order.get(m["id"], 99))


def saved_result(sid: str):
    p = S.SAMPLES_DIR / "results" / f"{sid}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- standard routes
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def info(request: Request):
    key, _ = keys.resolve_key()
    owner, payload = peek_owner(request)
    left = db.slots_left(owner) if owner else S.HOURLY_SCANS
    demo = None
    if S.DEMO_MODE:
        runs_left = max(0, int(payload.get("runs", 0)) - db.demo_runs_used(owner)) if payload else 0
        demo = {"ticket_valid": bool(payload), "runs_left": runs_left,
                "daily_cap_reached": db.demo_daily_count() >= S.DEMO_DAILY_CAP}
    return {"name": S.PROJECT_NAME, "slug": S.PROJECT_SLUG, "number": S.PROJECT_NUMBER, "version": S.VERSION,
            "mode": "demo" if S.DEMO_MODE else "local",
            "ai": {"provider": "groq", "ready": bool(key), "model": S.GROQ_MODEL},
            "limits": {"max_upload_mb": S.DEMO_MAX_UPLOAD_MB if S.DEMO_MODE else S.MAX_UPLOAD_MB,
                       "hourly_scans": S.HOURLY_SCANS, "scans_left": left},
            "demo": demo, "profiles": pipeline.PROFILES}


@app.get("/api/samples")
async def samples():
    return {"samples": [{"id": m["id"], "title": m["title"], "input_text": m.get("input_text", ""),
                         "asset_profile": m.get("asset_profile", pipeline.DEFAULT_PROFILE),
                         "has_saved_result": saved_result(m["id"]) is not None} for m in sample_meta()]}


@app.get("/api/samples/{sid}")
async def sample(sid: str):
    m = next((m for m in sample_meta() if m["id"] == sid), None)
    if not m:
        raise AppError(404, "NOT_FOUND", "That sample doesn't exist.")
    return {"id": m["id"], "title": m["title"], "input_text": m.get("input_text", ""),
            "asset_profile": m.get("asset_profile", pipeline.DEFAULT_PROFILE), "result": saved_result(sid)}


@app.get("/api/keys")
async def keys_get(request: Request):
    keys_guard(request)
    key, source = keys.resolve_key()
    return {"groq": {"configured": bool(key), "source": source, "masked": keys.mask(key) if key else ""}}


@app.post("/api/keys")
async def keys_set(request: Request):
    keys_guard(request)
    body = await read_json(request)
    k = body.get("groq_api_key")
    if not keys.is_usable_key(k):
        raise AppError(400, "BAD_INPUT", "That doesn't look like a Groq key. It should start with gsk_ and have no spaces.")
    db.set_saved_key(k)
    return {"saved": ["groq"]}


@app.delete("/api/keys/groq")
async def keys_delete(request: Request):
    keys_guard(request)
    db.delete_saved_key()
    return {"removed": "groq"}


# ---------------------------------------------------------------- history
@app.get("/api/history")
async def history(request: Request):
    owner, _ = peek_owner(request)
    return {"items": db.list_history(owner) if owner else []}


@app.get("/api/history/{hid}")
async def history_item(hid: int, request: Request):
    owner, _ = peek_owner(request)
    item = db.get_history(owner, hid) if owner else None
    if not item:
        raise AppError(404, "NOT_FOUND", "That history item doesn't exist.")
    return item


@app.delete("/api/history")
async def history_clear(request: Request):
    owner, _ = peek_owner(request)
    if owner:
        db.clear_history(owner)
    return {"cleared": True}


# ---------------------------------------------------------------- feature routes
def _begin(request: Request):
    owner, payload = gate(request)
    slot = db.reserve_slot(owner)
    if slot is None:
        raise AppError(429, "RATE_LIMITED", RATE_MSG)
    return owner, payload, slot


def _finish_ok(owner: str, payload, kind: str, title: str, result: dict) -> int:
    if S.DEMO_MODE and payload:
        db.demo_record_run(owner)
    hid = db.add_history(owner, kind, title, result)
    return hid


async def _execute_analyze(loc: str, prof: str, emit):
    async with geo.new_client() as client:
        r = await pipeline.gather(client, loc, prof, emit, check_ai=check_ai_ready)
    return await pipeline.strategist(r, emit)


@app.post("/api/analyze")
async def analyze(request: Request, stream: int = 0):
    body = await read_json(request)
    loc, prof = pipeline.validate_query(body.get("location"), body.get("profile"))
    owner, payload, slot = _begin(request)

    async def run(emit):
        ok = False
        try:
            r = await _execute_analyze(loc, prof, emit)
            r["history_id"] = _finish_ok(owner, payload, "analyze",
                                         f"{r['location']['name']}, {r['location']['admin1']}", r)
            ok = True
            r["scans_left"] = db.slots_left(owner)
            return r
        finally:
            if not ok:
                db.refund_slot(slot)

    if not stream:
        r = await run(None)
        return r

    q: asyncio.Queue = asyncio.Queue()

    async def emit(e):
        await q.put(e)

    async def worker():
        try:
            r = await run(emit)
            await q.put({"event": "result", "result": r})
        except AppError as e:
            await q.put({"event": "error", "status": e.status, **e.body(), "scans_left": db.slots_left(owner)})
        except Exception as e:  # never leak provider text
            log.exception("analyze failed: %s", type(e).__name__)
            await q.put({"event": "error", "status": 500, "code": "AI_FAILED",
                         "error": "Something went wrong while analysing. Please try again.",
                         "scans_left": db.slots_left(owner)})
        finally:
            await q.put(None)

    asyncio.create_task(worker())

    async def gen():
        while True:
            e = await q.get()
            if e is None:
                break
            yield json.dumps(e) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.post("/api/compare")
async def compare(request: Request):
    body = await read_json(request)
    loc_a, prof = pipeline.validate_query(body.get("location_a"), body.get("profile"))
    loc_b, _ = pipeline.validate_query(body.get("location_b"), body.get("profile"))
    owner, payload, slot = _begin(request)
    ok = False
    try:
        check_ai_ready()
        async with geo.new_client() as client:
            a, b = await asyncio.gather(pipeline.gather(client, loc_a, prof), pipeline.gather(client, loc_b, prof))
        cmp_, model = await pipeline.compare_strategist(a, b)
        result = {"a": a, "b": b, "comparison": cmp_, "model": model, "advisory_source": "groq"}
        result["history_id"] = _finish_ok(owner, payload, "compare",
                                          f"{a['location']['name']} vs {b['location']['name']}", result)
        ok = True
        result["scans_left"] = db.slots_left(owner)
        return result
    finally:
        if not ok:
            db.refund_slot(slot)


# ---------------------------------------------------------------- frontend (relative URLs only)
app.mount("/", StaticFiles(directory=str(S.STATIC_DIR), html=True), name="static")
