"""All Groq access lives here: retries, model fallback, JSON validation, plain-English errors."""
import asyncio
import json
import logging
import random
import re
from typing import Type

import httpx
from pydantic import BaseModel, ValidationError

from .config import S
from .errors import AppError
from .keys import resolve_key

log = logging.getLogger("groq")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = 45.0
RETRIES = 2  # retries after the first try = 3 attempts per model (waits 1s, 2s + jitter)
RETRYABLE = {429, 500, 502, 503, 504}

MSG_NOT_CONFIGURED = "No Groq key is set yet. Open API Keys and paste a free key from console.groq.com/keys."
MSG_KEY_INVALID = "Your Groq key was rejected. Open API Keys and paste a fresh key from console.groq.com/keys."
MSG_BUSY = "The AI service is busy right now. Wait a minute and try again."
MSG_FAILED = "The AI couldn't process this input."


class _Retryable(Exception):
    def __init__(self, wait=None):
        self.wait = wait


class _Fatal(Exception):
    pass


class _BadKey(Exception):
    pass


def _scrub(text: str, key: str) -> str:
    text = text or ""
    if key:
        text = text.replace(key, "[key]")
    return re.sub(r"gsk_[A-Za-z0-9]+", "[key]", text)[:400]


def _retry_after(body: str):
    m = re.search(r"try again in (\d+(?:\.\d+)?)s", body or "", re.I)
    if m:
        x = float(m.group(1))
        if x <= 20:
            return x + 0.25
    return None


def extract_json(text: str) -> dict:
    """Strip <think> blocks and code fences, then parse the first JSON object."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"<think>.*", "", text, flags=re.S | re.I)
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


async def _post(client: httpx.AsyncClient, key: str, model: str, messages: list, max_tokens: int) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0.3,
               "max_completion_tokens": max_tokens, "response_format": {"type": "json_object"}}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"
    try:
        r = await client.post(GROQ_URL, json=payload, headers={"Authorization": f"Bearer {key}"})
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise _Retryable() from e
    if r.status_code == 200:
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise _Fatal("unexpected response shape") from e
    if r.status_code == 401:
        raise _BadKey()
    if r.status_code in RETRYABLE:
        raise _Retryable(_retry_after(r.text))
    log.warning("Groq %s on %s: %s", r.status_code, model, _scrub(r.text, key))
    raise _Fatal(f"HTTP {r.status_code}")


async def chat_json(system: str, user: str, schema: Type[BaseModel], max_tokens: int = 3500):
    """Return (validated model instance, model_name). Raises AppError with the standard codes."""
    key, _ = resolve_key()
    if not key:
        raise AppError(503, "AI_NOT_CONFIGURED", MSG_NOT_CONFIGURED)

    models = [S.GROQ_MODEL]
    if S.GROQ_FALLBACK_MODEL and S.GROQ_FALLBACK_MODEL != S.GROQ_MODEL:
        models.append(S.GROQ_FALLBACK_MODEL)

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    busy = False
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for model in models:
            repaired = False
            attempt = 0
            while True:
                try:
                    content = await _post(client, key, model, messages, max_tokens)
                except _BadKey:
                    raise AppError(502, "AI_KEY_INVALID", MSG_KEY_INVALID)
                except _Retryable as e:
                    busy = True
                    if attempt >= RETRIES:
                        log.warning("Groq model %s still busy after %d retries", model, RETRIES)
                        break
                    wait = e.wait if e.wait is not None else (2 ** attempt) + random.uniform(0, 0.5)
                    attempt += 1
                    await asyncio.sleep(wait)
                    continue
                except _Fatal as e:
                    busy = False
                    log.warning("Groq model %s failed: %s", model, e)
                    break
                try:
                    data = schema.model_validate(extract_json(content))
                    return data, model
                except (ValueError, ValidationError) as e:
                    busy = False
                    log.warning("Groq model %s returned invalid JSON (%s)", model, type(e).__name__)
                    if not repaired:
                        repaired = True
                        messages = messages + [
                            {"role": "assistant", "content": (content or "")[:1500]},
                            {"role": "user", "content": "That was not valid. Reply again with ONLY the JSON object "
                                                        "matching the requested schema, no extra text."}]
                        continue
                    break
    if busy:
        raise AppError(503, "AI_BUSY", MSG_BUSY)
    raise AppError(502, "AI_FAILED", MSG_FAILED)
