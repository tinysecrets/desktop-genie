#!/usr/bin/env python3
"""GENIE Puter.js free bridge.

Puter's OpenAI/Anthropic-compatible REST endpoints (/puterai/openai/v1/*,
/puterai/anthropic/v1/messages) require a PAID plan; free accounts receive
`402 subscription_required`. The free path is `puter.ai.*` through an
authenticated Puter.js environment.

This bridge runs Puter.js inside a persistent local Chromium profile:

    genie puter login     # one-time, opens a visible browser to sign in
    genie puter status    # show bridge/session state
    genie puter models    # live catalog via puter.ai.listModels()

After the one-time sign-in, every later call runs headless against the saved
session. The model catalog is discovered dynamically with
`puter.ai.listModels()` - no hard-coded assumptions about which models exist.

GENIE routing uses this free bridge only. Paid REST fallback is intentionally
disabled in the router, even if a paid-provider token exists.
"""

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".config" / "genie"
STATE = ROOT / "state"
PROFILE_DIR = Path(os.environ.get("PUTER_PROFILE", str(ROOT / "puter-profile")))
STATUS_CACHE = STATE / "puter-bridge.json"
MODEL_CACHE = STATE / "puter-models.json"

BRIDGE_URL = "https://puter.com/"
STATUS_TTL = 900          # seconds a probe result stays fresh for doctor/router
MODEL_TTL = 3600          # seconds the model catalog cache stays fresh
LOGIN_WAIT_SECONDS = 300  # max time to wait for the human during login
CHAT_TIMEOUT_MS = 600000  # flagship brains can think for a while

_lock = threading.Lock()
_pw = None
_context = None
_page = None


class PuterBridgeError(RuntimeError):
    """Bridge-level failure. The message is a stable machine-readable code."""


# --------------------------------------------------------------------------
# Browser lifecycle
# --------------------------------------------------------------------------

def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except Exception:
        raise PuterBridgeError("PUTER_BRIDGE_NO_PLAYWRIGHT")


def _teardown():
    global _pw, _context, _page
    for closer, obj in ((_context, _context), (_pw, _pw)):
        try:
            if obj is not None:
                if closer is _context:
                    obj.close()
                else:
                    obj.stop()
        except Exception:
            pass
    _pw = None
    _context = None
    _page = None


def _open(headless=True):
    """Open (or reuse) the persistent Puter browser session."""
    global _pw, _context, _page
    if _context is not None and _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except Exception:
            pass
        _teardown()

    sync_playwright = _sync_playwright()
    _pw = sync_playwright().start()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    kwargs = {
        "headless": bool(headless),
        "accept_downloads": False,
        "args": ["--no-first-run", "--no-default-browser-check"],
    }
    executable = os.environ.get("PUTER_BROWSER", "/usr/bin/google-chrome")
    if executable and Path(executable).exists():
        kwargs["executable_path"] = executable

    _context = _pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs)
    _page = _context.pages[0] if _context.pages else _context.new_page()
    return _page


def _ensure_page(headless=True):
    """Return a page on the Puter origin with the SDK ready."""
    page = _open(headless=headless)
    try:
        if "puter.com" not in (page.url or ""):
            page.goto(BRIDGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "() => typeof window.puter !== 'undefined'", timeout=30000
        )
    except PuterBridgeError:
        raise
    except Exception as exc:
        raise PuterBridgeError(f"PUTER_SDK_NOT_READY: {exc}")
    return page


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

_SIGNED_IN_JS = (
    "async () => { try { return !!(window.puter && await puter.auth.isSignedIn()); }"
    " catch (e) { return false; } }"
)


def _signed_in(page):
    try:
        return bool(page.evaluate(_SIGNED_IN_JS))
    except Exception:
        return False


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_json(path, data):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def status(force=False):
    """Probe the bridge. Cached so doctor/router probes stay cheap."""
    with _lock:
        cache = _read_json(STATUS_CACHE)
        if (
            not force
            and isinstance(cache, dict)
            and time.time() - float(cache.get("checked_at") or 0) < STATUS_TTL
        ):
            return cache

        result = {
            "checked_at": time.time(),
            "signed_in": False,
            "playwright": True,
            "profile": str(PROFILE_DIR),
            "error": None,
        }
        try:
            page = _ensure_page(headless=True)
            result["signed_in"] = _signed_in(page)
            if not result["signed_in"]:
                result["error"] = "PUTER_AUTH_REQUIRED"
        except PuterBridgeError as exc:
            result["error"] = str(exc)
            result["playwright"] = "NO_PLAYWRIGHT" not in str(exc)
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(STATUS_CACHE, result)
        return result


def available():
    """Quick router probe: is the free bridge usable right now?"""
    try:
        return bool(status().get("signed_in"))
    except Exception:
        return False


def login(timeout=LOGIN_WAIT_SECONDS):
    """Interactive one-time sign-in using a VISIBLE browser window.

    Robustness notes (from the live failure mode):
    - puter.auth.signIn() opens a popup and must be triggered by a user
      action; called programmatically the browser may block it or Puter
      rejects with popup_blocked. We still try it (it works on some builds),
      but the RELIABLE path is the built-in login UI on puter.com itself,
      which we open automatically after a short grace period.
    - If the human closes the window, we fail fast with a clear code
      instead of burning the whole timeout.
    """
    with _lock:
        page = _ensure_page(headless=False)
        if _signed_in(page):
            _write_json(STATUS_CACHE, {
                "checked_at": time.time(), "signed_in": True,
                "playwright": True, "profile": str(PROFILE_DIR), "error": None,
            })
            return {"ok": True, "already_signed_in": True}

        # Best effort: SDK popup sign-in (may be popup-blocked; harmless).
        try:
            page.evaluate(
                "() => { try { puter.auth.signIn(); } catch (e) {} }"
            )
        except Exception:
            pass

        start = time.time()
        deadline = start + timeout
        fallback_done = False
        last_tick = -1

        while time.time() < deadline:
            time.sleep(2)

            # Fail fast when the browser window was closed.
            try:
                if _page is None or _page.is_closed():
                    raise PuterBridgeError("PUTER_LOGIN_WINDOW_CLOSED")
            except PuterBridgeError:
                raise
            except Exception:
                raise PuterBridgeError("PUTER_LOGIN_WINDOW_CLOSED")

            if _signed_in(page):
                _write_json(STATUS_CACHE, {
                    "checked_at": time.time(), "signed_in": True,
                    "playwright": True, "profile": str(PROFILE_DIR), "error": None,
                })
                return {"ok": True, "already_signed_in": False}

            elapsed = int(time.time() - start)

            # Reliable fallback: the normal puter.com login UI in-page.
            # Signing into puter.com itself establishes the same session.
            if not fallback_done and elapsed >= 10:
                fallback_done = True
                try:
                    if "login" not in (page.url or ""):
                        page.goto("https://puter.com/login",
                                  wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass

            # Keep any auth popup visible and in front.
            try:
                for extra in (_context.pages if _context else []):
                    if extra is not _page and not extra.is_closed():
                        extra.bring_to_front()
            except Exception:
                pass

            if elapsed // 15 != last_tick:
                last_tick = elapsed // 15
                hint = ("complete the sign-in in the browser window"
                        if fallback_done else
                        "waiting for the Puter sign-in window")
                print(f"[puter login] {elapsed}s - {hint} "
                      f"({int(deadline - time.time())}s left)")

        raise PuterBridgeError("PUTER_LOGIN_TIMEOUT")


# --------------------------------------------------------------------------
# Model catalog (dynamic discovery via puter.ai.listModels)
# --------------------------------------------------------------------------

_LIST_MODELS_JS = (
    "async () => {"
    " if (!window.puter) throw new Error('PUTER_SDK_MISSING');"
    " const signed = await puter.auth.isSignedIn().catch(() => false);"
    " if (!signed) throw new Error('PUTER_AUTH_REQUIRED');"
    " return await puter.ai.listModels();"
    "}"
)


def list_models(force=False, max_age=MODEL_TTL):
    """Live Puter catalog, cached for an hour."""
    with _lock:
        cache = _read_json(MODEL_CACHE)
        if (
            not force
            and isinstance(cache, dict)
            and time.time() - float(cache.get("checked_at") or 0) < max_age
            and isinstance(cache.get("models"), list)
        ):
            return cache["models"]

        page = _ensure_page(headless=True)
        if not _signed_in(page):
            raise PuterBridgeError("PUTER_AUTH_REQUIRED")
        try:
            models = page.evaluate(_LIST_MODELS_JS)
        except Exception as exc:
            raise PuterBridgeError(f"PUTER_LIST_MODELS_FAILED: {exc}")
        if not isinstance(models, list):
            raise PuterBridgeError("PUTER_LIST_MODELS_EMPTY")

        _write_json(MODEL_CACHE, {"checked_at": time.time(), "models": models})
        return models


# --------------------------------------------------------------------------
# Verification: prove which brains Puter actually serves (no assumptions)
# --------------------------------------------------------------------------

TEAM_FILE = ROOT / "ai-team.json"
VERIFICATION_BRAINS = (
    "opus-55", "fable-51", "hermes-405b",
    "hermes-3-405b", "hermes-14b", "hermes-3-70b",
)


def verify(force=False):
    """Check the LIVE Puter catalog against GENIE's roster brains.

    Returns {"ok": bool, "models": total, "brains": {handle: {...}}}.
    Every entry records what the requested roster id actually resolved to,
    its live cost, and whether a zero-cost variant exists.
    """
    models = list_models(force=force)

    roster = {}
    try:
        data = json.loads(TEAM_FILE.read_text())
        for member in data.get("members") or []:
            if member.get("handle") in VERIFICATION_BRAINS:
                roster[member["handle"]] = member
    except Exception:
        pass

    brains = {}
    for handle in VERIFICATION_BRAINS:
        member = roster.get(handle) or {}
        requested = member.get("model") or ""
        resolved = resolve_model(requested, models)
        entry = next(
            (m for m in models if m.get("id") == resolved), None
        )
        base = (resolved or "").split(":")[0]
        free_variant = any(
            (m.get("id") or "").startswith(base + ":")
            and _model_cost(m) == 0
            for m in models
        ) if base else False
        brains[handle] = {
            "requested": requested,
            "resolved": resolved,
            "found": entry is not None,
            "free_variant": free_variant,
            "cost": (entry or {}).get("cost"),
            "context": (entry or {}).get("context"),
            "served_by": (entry or {}).get("provider"),
        }

    free_count = sum(1 for m in models if _model_cost(m) == 0)
    return {
        "ok": True,
        "models": len(models),
        "free_models": free_count,
        "brains": brains,
    }


def _model_cost(model):
    cost = model.get("cost")
    if not isinstance(cost, dict) or "input" not in cost or "output" not in cost:
        return None
    try:
        input_cost = float(cost["input"])
        output_cost = float(cost["output"])
    except (TypeError, ValueError):
        return None
    if input_cost < 0 or output_cost < 0:
        return None
    return input_cost + output_cost


def _normalize_id(value):
    value = (value or "").lower()
    value = value.split("/")[-1]
    for ch in "-_.: ":
        value = value.replace(ch, "")
    return value


def resolve_model(requested, models=None):
    """Map a roster model id onto the live Puter catalog.

    Preference order: exact id, alias, then mutual substring match. Only
    catalog entries with explicit numeric zero input AND output cost are
    eligible. Unknown or paid-only requests fail closed so the router can
    escalate to another free tier.
    """
    if not requested or requested == "auto":
        return None

    if models is None:
        cache = _read_json(MODEL_CACHE)
        models = cache.get("models") if isinstance(cache, dict) else None
    if not models:
        return None

    wanted = _normalize_id(requested)

    def candidates():
        found = []
        for model in models:
            model_id = model.get("id") or ""
            aliases = model.get("aliases") or []
            names = [model_id] + list(aliases) + [model.get("name") or ""]
            if requested in names or wanted == _normalize_id(model_id):
                found.append(model)
                continue
            if any(wanted and wanted == _normalize_id(n) for n in names):
                found.append(model)
                continue
            if (
                wanted
                and (_normalize_id(model_id) in wanted or wanted in _normalize_id(model_id))
                and len(wanted) >= 6
            ):
                found.append(model)
        return found

    pool = candidates()
    if not pool:
        return None

    # Free-first: prefer zero-cost variants of the same base model.
    base_ids = {_normalize_id(m.get("id", "").split(":")[0]) for m in pool}
    expanded = list(pool)
    for model in models:
        model_id = model.get("id") or ""
        if ":" in model_id and _normalize_id(model_id.split(":")[0]) in base_ids:
            expanded.append(model)

    free = [m for m in expanded if _model_cost(m) == 0]
    if not free:
        return None
    chosen = free[0]
    return chosen.get("id") or None


# --------------------------------------------------------------------------
# Chat (the free path for GENIE's puter brains)
# --------------------------------------------------------------------------

_CHAT_JS = (
    "async ({messages, model, tools, max_tokens, temperature}) => {"
    " if (!window.puter) throw new Error('PUTER_SDK_MISSING');"
    " const signed = await puter.auth.isSignedIn().catch(() => false);"
    " if (!signed) throw new Error('PUTER_AUTH_REQUIRED');"
    " const options = {normalize: true};"
    " if (model) options.model = model;"
    " if (typeof max_tokens === 'number') options.max_tokens = max_tokens;"
    " if (typeof temperature === 'number') options.temperature = temperature;"
    " if (Array.isArray(tools) && tools.length) {"
    "   options.tools = tools.filter(t => t && t.type === 'function' && t.function && t.function.name);"
    " }"
    " const resp = await puter.ai.chat(messages, false, options);"
    " const message = (resp && resp.message) ? resp.message : (resp || {});"
    " let content = message.content;"
    " if (content !== null && content !== undefined && typeof content !== 'string') {"
    "   try { content = JSON.stringify(content); } catch (e) { content = String(content); }"
    " }"
    " const tool_calls = Array.isArray(message.tool_calls) ? message.tool_calls.map((call, index) => {"
    "   const fn = (call && call.function) ? call.function : {};"
    "   let args = fn.arguments;"
    "   if (args !== undefined && args !== null && typeof args !== 'string') {"
    "     try { args = JSON.stringify(args); } catch (e) { args = '{}'; }"
    "   }"
    "   return {id: (call && call.id) ? call.id : ('puter_call_' + index),"
    "           type: 'function',"
    "           function: {name: fn.name || null, arguments: args || '{}'}};"
    " }) : [];"
    " return {content: content || '', tool_calls: tool_calls,"
    "         finish_reason: (resp && resp.finish_reason) ? resp.finish_reason : null,"
    "         served_model: (resp && resp.model) ? resp.model : null};"
    "}"
)


def chat(messages, model=None, tools=None, max_tokens=12000, temperature=0.2):
    """Run one chat turn through puter.ai.chat. Returns an OpenAI-style message.

    Raises PuterBridgeError with a stable code on bridge/auth problems so the
    GENIE router can escalate to the next brain.
    """
    with _lock:
        page = _ensure_page(headless=True)
        if not _signed_in(page):
            raise PuterBridgeError("PUTER_AUTH_REQUIRED")

        payload = {
            "messages": messages,
            "model": model,
            "tools": tools or [],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            result = page.evaluate(_CHAT_JS, payload, timeout=CHAT_TIMEOUT_MS)
        except Exception as exc:
            text = str(exc)
            for code in ("PUTER_AUTH_REQUIRED", "PUTER_SDK_MISSING"):
                if code in text:
                    raise PuterBridgeError(code)
            raise PuterBridgeError(f"PUTER_CHAT_FAILED: {text[:500]}")

        if not isinstance(result, dict):
            raise PuterBridgeError("PUTER_EMPTY_RESPONSE")
        return {
            "content": result.get("content") or "",
            "tool_calls": result.get("tool_calls") or None,
            "served_model": result.get("served_model"),
            "finish_reason": result.get("finish_reason"),
        }


def close():
    """Shut the bridge browser down cleanly."""
    with _lock:
        _teardown()
