"""Thin wrapper around the Google GenAI SDK.

* The API key is read from the environment only and never logged or displayed.
* "Configured" (key present) is NOT the same as "connected": `test_connection()` makes a real,
  tiny request and records the verified status that the UI shows.
* If the configured model is unavailable (404 / retired), a short fallback list is tried.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import config

MODEL_FALLBACKS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-2.5-flash"]
REQUEST_TIMEOUT_MS = 45_000


class GeminiNotConfigured(Exception):
    pass


class GeminiError(Exception):
    """Safe-to-display error (never contains the API key)."""


_lock = threading.Lock()
_status: dict = {"state": "unknown", "message": "", "model": "", "checked_at": None, "key_fp": ""}
_working_model: str = ""


def is_configured() -> bool:
    return config.gemini_configured()


def _fingerprint() -> str:
    key = config.current_gemini_key()
    return hashlib.sha256(key.encode()).hexdigest()[:10] if key else ""


def status() -> dict:
    """Current verified status: state in not_configured | unknown | ok | error."""
    with _lock:
        st = dict(_status)
    if not is_configured():
        return {**st, "state": "not_configured", "message": config.GEMINI_MISSING_MSG}
    if st["key_fp"] != _fingerprint():          # key changed since last check
        return {**st, "state": "unknown", "message": "API key found but not verified yet. Use Settings → Test Gemini connection."}
    return st


def _set_status(state: str, message: str, model: str = "") -> None:
    with _lock:
        _status.update(state=state, message=message, model=model, key_fp=_fingerprint(),
                       checked_at=time.strftime("%H:%M:%S"))


def reset_state() -> None:
    """Forget cached status/model (used by tests and when the key changes)."""
    global _working_model
    with _lock:
        _status.update(state="unknown", message="", model="", checked_at=None, key_fp="")
        _working_model = ""


def _client():
    if not is_configured():
        raise GeminiNotConfigured(config.GEMINI_MISSING_MSG)
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise GeminiError("The google-genai package is not installed. Run: pip install -r requirements.txt") from exc
    opts = {"timeout": REQUEST_TIMEOUT_MS}
    if config.gemini_base_url():
        opts["base_url"] = config.gemini_base_url()
    return genai.Client(api_key=config.current_gemini_key(), http_options=types.HttpOptions(**opts))


def _scrub(text: str) -> str:
    key = config.current_gemini_key()
    return text.replace(key, "***") if key else text


def _is_model_problem(exc: Exception) -> bool:
    low = str(exc).lower()
    return ("404" in low or "not_found" in low or "not found" in low or "no longer available" in low
            or "is not supported" in low or "unsupported model" in low or "has been shut down" in low)


def _wrap(exc: Exception) -> GeminiError:
    msg = _scrub(str(exc))
    low = msg.lower()
    detail = re.sub(r"\s+", " ", msg)[:160]
    if "api key" in low or "api_key_invalid" in low or "401" in low or "unauthenticated" in low:
        return GeminiError("Gemini rejected the API key (invalid or expired). Create a new key in Google AI Studio and update GEMINI_API_KEY.")
    if "403" in low or "permission" in low:
        return GeminiError("Gemini denied permission for this key/project (check that the Gemini API is enabled for it and the region is supported).")
    if "429" in low or "quota" in low or "resource_exhausted" in low or "rate" in low and "limit" in low:
        return GeminiError("Gemini rate limit or free-tier quota reached. Wait a minute and try again.")
    if _is_model_problem(exc):
        return GeminiError(f"The Gemini model is not available for this key ({config.current_gemini_model()}). Set GEMINI_MODEL to a current model such as gemini-flash-latest.")
    if any(w in low for w in ("connect", "timeout", "timed out", "name resolution", "network", "ssl")):
        return GeminiError("Could not reach Gemini (network, firewall, VPN or proxy problem). Check your internet connection.")
    return GeminiError(f"Gemini request failed ({type(exc).__name__}: {detail})")


def candidate_models() -> list[str]:
    order = [_working_model, config.current_gemini_model(), *MODEL_FALLBACKS]
    seen, out = set(), []
    for m in order:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _discover_model(client) -> str | None:
    """Ask the API which models this key can use and pick a Flash model that supports generateContent."""
    try:
        names = []
        for m in client.models.list():
            actions = getattr(m, "supported_actions", None) or []
            name = (getattr(m, "name", "") or "").replace("models/", "")
            if "generateContent" in actions and "flash" in name and not any(x in name for x in ("image", "tts", "live", "audio", "embedding", "robotics")):
                names.append(name)
        stable = [n for n in names if "preview" not in n and "exp" not in n]
        pool = stable or names
        return sorted(pool, reverse=True)[0] if pool else None
    except Exception:
        return None


def _call(fn):
    """Run fn(client, model) over candidate models; remember the first model that works."""
    global _working_model
    client = _client()
    last: Exception | None = None
    tried = []
    for model in candidate_models():
        tried.append(model)
        try:
            out = fn(client, model)
            with _lock:
                _working_model = model
            return out
        except GeminiError:
            raise
        except Exception as exc:
            last = exc
            if _is_model_problem(exc):
                continue
            raise _wrap(exc) from exc
    found = _discover_model(client)
    if found and found not in tried:
        try:
            out = fn(client, found)
            with _lock:
                _working_model = found
            return out
        except Exception as exc:
            last = exc
    raise _wrap(last) if last else GeminiError("No Gemini model could be used.")


def _extract_text(resp) -> str:
    try:
        text = resp.text
    except Exception:
        text = None
    if text:
        return text.strip()
    reason = ""
    try:
        reason = str(resp.candidates[0].finish_reason)
    except Exception:
        pass
    raise GeminiError(f"Gemini returned no text{' (' + reason + ')' if reason else ''}. Try rephrasing or try again.")


def generate_text(prompt: str, system: str | None = None, temperature: float = 0.4) -> str:
    from google.genai import types

    def run(client, model):
        resp = client.models.generate_content(model=model, contents=prompt,
                                              config=types.GenerateContentConfig(system_instruction=system, temperature=temperature))
        return _extract_text(resp)
    return _call(run)


def generate_with_tools(prompt: str, tools: list, system: str | None = None) -> str:
    """Let Gemini call the given Python functions (automatic function calling)."""
    from google.genai import types

    def run(client, model):
        resp = client.models.generate_content(model=model, contents=prompt,
                                              config=types.GenerateContentConfig(system_instruction=system, tools=tools, temperature=0.2))
        return _extract_text(resp)
    return _call(run)


def test_connection() -> dict:
    """Make one real, tiny request. Returns {ok, message, model} and updates the cached status."""
    if not is_configured():
        reset_state()
        return {"ok": False, "message": config.GEMINI_MISSING_MSG, "model": ""}
    try:
        text = generate_text("Reply with exactly one word: OK", temperature=0.0)
        model = _working_model or config.current_gemini_model()
        msg = f"Connected. Model in use: {model}. Test reply: {text[:30]!r}"
        _set_status("ok", msg, model)
        return {"ok": True, "message": msg, "model": model}
    except GeminiError as exc:
        _set_status("error", str(exc))
        return {"ok": False, "message": str(exc), "model": ""}
    except Exception as exc:  # pragma: no cover
        err = _wrap(exc)
        _set_status("error", str(err))
        return {"ok": False, "message": str(err), "model": ""}


_verifying = False


def verify_in_background() -> bool:
    """Start one background connection test if a key is set and it has not been verified yet."""
    global _verifying
    if not is_configured() or status()["state"] in ("ok", "error") or _verifying:
        return False
    _verifying = True

    def work():
        global _verifying
        try:
            test_connection()
        finally:
            _verifying = False
    threading.Thread(target=work, daemon=True).start()
    return True


def parse_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating ```json fences."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.S)
        if not m:
            raise GeminiError("Gemini returned an unexpected format. Please try again.")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise GeminiError("Gemini returned an unexpected format. Please try again.") from exc
    if not isinstance(data, dict):
        raise GeminiError("Gemini returned an unexpected format. Please try again.")
    return data
