#!/usr/bin/env python3
import json, os, socket, subprocess, time
from pathlib import Path

CONFIG = Path.home() / ".config" / "genie"
SOCKET = CONFIG / "puter-bridge.sock"
NODE = Path(__file__).with_name("puter_bridge_node.js")
PROFILE = CONFIG / "puter-profile"
PUPPETEER_ROOT = os.environ.get(
    "GENIE_PUPPETEER_ROOT",
    "/home/justin/.npm-global/lib/node_modules/@wonderwhy-er/desktop-commander/node_modules/puppeteer",
)

class PuterBridgeError(RuntimeError):
    pass

PUTER_LOGIN_WINDOW_CLOSED = "PUTER_LOGIN_WINDOW_CLOSED"

def _env():
    env = os.environ.copy()
    env["GENIE_PUTER_PROFILE"] = str(PROFILE)
    env["GENIE_PUTER_SOCKET"] = str(SOCKET)
    env["GENIE_PUPPETEER_ROOT"] = PUPPETEER_ROOT
    return env

def _daemon_running():
    if not SOCKET.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(str(SOCKET))
        s.close()
        return True
    except OSError:
        return False

def _ensure_daemon():
    if _daemon_running():
        return
    CONFIG.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        ["node", str(NODE), "daemon"],
        env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if _daemon_running():
            return
        time.sleep(.2)
    raise PuterBridgeError("PUTER_BRIDGE_UNAVAILABLE")

def _request(payload, timeout=180):
    _ensure_daemon()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(str(SOCKET))
    s.settimeout(timeout)
    s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
    data = b""
    try:
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    if not data:
        raise PuterBridgeError("PUTER_BRIDGE_EMPTY_RESPONSE")
    result = json.loads(data.decode())
    if not result.get("ok"):
        raise PuterBridgeError(result.get("error") or "PUTER_BRIDGE_ERROR")
    return result.get("result")

def status():
    try:
        result = _request({"op":"status"}, timeout=30)
        return {"bridge": True, "authenticated": bool(result.get("authenticated"))}
    except Exception as exc:
        return {"bridge": False, "authenticated": False, "error": str(exc)}

def models():
    result = _request({"op":"models"}, timeout=60)
    return result

def _free(m):
    mid = str(m.get("id") or "").lower()
    if mid.endswith(":free"):
        return True
    cost = m.get("cost") or m.get("pricing") or {}
    try:
        return float(cost.get("input", 1) or 0) == 0 and float(cost.get("output", 1) or 0) == 0
    except Exception:
        return False

def resolve_model(requested, catalog):
    requested = str(requested or "").strip()
    models = catalog.get("models") or []
    free = [m for m in models if _free(m)]
    if not free:
        raise PuterBridgeError("PUTER_NO_FREE_MODELS")
    exact_names = [requested, requested.split("/")[-1]]
    # Prefer an explicit :free variant whenever the catalog exposes one.
    for want in exact_names:
        free_want = want if want.lower().endswith(":free") else want + ":free"
        for m in free:
            if str(m.get("id","")).lower() == free_want.lower():
                return str(m["id"])
    for want in exact_names:
        for m in free:
            if str(m.get("id","")).lower() == want.lower():
                return str(m["id"])
    for m in free:
        mid = str(m.get("id") or "")
        if mid.lower().split(":")[0] in {x.lower().removesuffix(":free") for x in exact_names}:
            return mid
    # Fuzzy fallback for the roster's named brains.
    needle = requested.lower().replace(":free","")
    for m in free:
        hay = (str(m.get("id",""))+" "+str(m.get("name",""))).lower()
        if needle and (needle in hay or all(part in hay for part in needle.replace("/"," ").replace("-"," ").split()[:2])):
            return str(m["id"])
    raise PuterBridgeError("PUTER_MODEL_UNAVAILABLE:" + requested)

def chat(messages, tools=None, model=None, max_tokens=12000, temperature=.2):
    st = status()
    if not st.get("authenticated"):
        raise PuterBridgeError("PUTER_AUTH_REQUIRED")
    catalog = models()
    chosen = resolve_model(model, catalog)
    return _request({
        "op":"chat", "model":chosen, "messages":messages, "tools":tools or [],
        "max_tokens":max_tokens, "temperature":temperature
    }, timeout=240)

def login(timeout=240):
    # Stop any headless daemon first so Chromium can reuse the persistent profile.
    try:
        if SOCKET.exists():
            import signal
            # Find only our bridge node process; do not kill unrelated node processes.
            subprocess.run(
                ["pkill","-TERM","-f",f"node {str(NODE)} daemon"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
            )
            time.sleep(.5)
    except Exception:
        pass
    proc = subprocess.Popen(
        ["node", str(NODE), "login"], env=_env(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
    )
    deadline = time.time() + timeout
    final = None
    try:
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                line = line.rstrip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        final = json.loads(line)
                    except Exception:
                        pass
                else:
                    print(line, flush=True)
                continue
            if proc.poll() is not None:
                break
            time.sleep(.2)
        if proc.poll() is None:
            proc.kill()
            raise PuterBridgeError("PUTER_LOGIN_TIMEOUT")
        if proc.returncode:
            raise PuterBridgeError((final or {}).get("error") or "PUTER_LOGIN_FAILED")
        return final or {"authenticated": True}
    finally:
        if proc.poll() is None:
            proc.kill()

def _model_cost(model):
    cost = model.get("cost") or model.get("pricing") or {}
    try:
        if isinstance(cost, dict):
            return float(cost.get("input", 0) or 0) + float(cost.get("output", 0) or 0)
        return float(cost or 0)
    except Exception:
        return 0.0

TEAM_FILE = CONFIG / "ai-team.json"
VERIFICATION_BRAINS = ("opus-55", "fable-51", "hermes-405b", "hermes-3-405b", "hermes-14b", "hermes-3-70b")

def verify(force=False):
    catalog_data = _request({"op":"models"}, timeout=90) if force else models()
    catalog = catalog_data.get("models") if isinstance(catalog_data, dict) else catalog_data
    catalog = catalog or []
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
        try:
            resolved = resolve_model(requested, {"models": catalog})
            entry = next((m for m in catalog if m.get("id") == resolved), None)
            base = (resolved or "").split(":")[0]
            free_variant = any(
                (m.get("id") or "").startswith(base + ":") and _model_cost(m) == 0
                for m in catalog
            ) if base else False
            brains[handle] = {
                "requested": requested, "resolved": resolved, "found": entry is not None,
                "free_variant": free_variant, "cost": (entry or {}).get("cost"),
                "context": (entry or {}).get("context"), "served_by": (entry or {}).get("provider"),
            }
        except Exception as exc:
            brains[handle] = {"requested": requested, "resolved": None, "found": False,
                              "free_variant": False, "error": str(exc)}
    return {"ok": True, "models": len(catalog),
            "free_models": sum(1 for m in catalog if _model_cost(m) == 0),
            "brains": brains}

def free_models():
    data = models()
    return [m for m in (data.get("models") or []) if _free(m)]

if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
