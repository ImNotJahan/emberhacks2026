"""One structured Gemini call, timed. Everything model-facing goes through here."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from google import genai
from google.genai import errors, types

from judge.env import load_env

load_env()

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_client: Optional[genai.Client] = None


def client() -> genai.Client:
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set — add it to .env")
        _client = genai.Client(api_key=key)
    return _client


@dataclass
class CallResult:
    data: Any                 # parsed JSON (or text, for unstructured calls)
    raw: str
    latency_ms: int
    model: str


def call(
    system: str,
    user: str,
    schema: Optional[dict] = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_output_tokens: int = 1024,
    retries: int = 2,
) -> CallResult:
    """Run one generate_content call. With `schema`, the response is JSON
    constrained to it and returned parsed. Transport errors (429/5xx) are
    retried; a malformed response is NOT retried — it raises, so callers
    see it."""
    cfg = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        # Flash thinking adds seconds; the judge's reasoning lives in the
        # schema fields instead.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    if schema is not None:
        cfg.response_mime_type = "application/json"
        cfg.response_json_schema = schema

    attempt = 0
    t0 = time.perf_counter()   # latency includes retries: it's what the user waits
    while True:
        try:
            resp = client().models.generate_content(model=model, contents=user, config=cfg)
            break
        except (errors.ServerError, errors.ClientError) as e:
            code = getattr(e, "code", None)
            if attempt >= retries or (isinstance(e, errors.ClientError) and code != 429):
                raise
            attempt += 1
            time.sleep(1.5 * attempt)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    raw = resp.text or ""
    data: Any = json.loads(raw) if schema is not None else raw.strip()
    return CallResult(data=data, raw=raw, latency_ms=latency_ms, model=model)
