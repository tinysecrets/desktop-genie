#!/usr/bin/env python3
"""GENIE workstation operator layer.

Turns GENIE from a tool collection into an autonomous workstation operator.
Priority stack (from the operator roadmap):
    window -> service -> netdiag -> android -> watcher/event bus
    -> watchdog (self-healing) -> memory -> mission (workflow)
    -> policy/audit (approval + trail) -> redaction

Every tool is side-effect aware, audited and redaction-safe.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
import sys
import shlex
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path.home() / ".config" / "genie"
STATE = ROOT / "state"
STATE.mkdir(parents=True, exist_ok=True)
DB_FILE = STATE / "genie.db"
SHOTS = Path.home() / "Pictures" / "genie"
SHOTS.mkdir(parents=True, exist_ok=True)

MAX_OUT = 20000


# --------------------------------------------------------------------------
# redaction layer  (item 23)
# --------------------------------------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r"\b(sk|or|ghp|gho|ghu|ghs|ghr|github_pat|xox[baprs])[-_][A-Za-z0-9_\-]{12,}\b"), "[REDACTED:api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|authorization|private[_-]?key|client[_-]?secret)\b(\s*[:=]\s*)(\"?)([^\s\"',}]+)(\3)"), r"\1\2\3[REDACTED]\3"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED:private-key]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b(?=:[^:]*?(?:pass|pwd|secret|key))"), "[REDACTED:cred]"),
]


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact(obj):
    """Recursively redact strings anywhere in a tool result."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _is_secret_key(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


def _is_secret_key(key: str) -> bool:
    k = str(key).lower()
    return any(t in k for t in ("password", "passwd", "secret", "token", "api_key", "apikey", "private_key"))


# --------------------------------------------------------------------------
# db / event bus / audit
# --------------------------------------------------------------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_FILE), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS kv(
            ns TEXT, key TEXT, value TEXT, tags TEXT, updated REAL,
            PRIMARY KEY(ns, key));
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
            source TEXT, subject TEXT, severity TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS watch_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, path TEXT,
            event TEXT, pattern TEXT, action TEXT, enabled INTEGER,
            created REAL, fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS heal_rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, category TEXT,
            spec TEXT, steps TEXT, enabled INTEGER, created REAL,
            last_run REAL, last_status TEXT, repairs INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS missions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, objective TEXT,
            steps TEXT, status TEXT, created REAL, updated REAL, result TEXT);
        CREATE TABLE IF NOT EXISTS policies(
            id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, pattern TEXT,
            category TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS approvals(
            id TEXT PRIMARY KEY, tool TEXT, args TEXT, status TEXT,
            created REAL, resolved REAL, note TEXT);
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, goal TEXT,
            tool TEXT, args TEXT, result TEXT, verification TEXT,
            files TEXT, commands TEXT, rollback TEXT);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
        """
    )
    return conn


def emit(kind: str, source: str = "genie", subject: str = "", severity: str = "info", payload=None) -> dict:
    row = {
        "ts": time.time(), "kind": kind, "source": source, "subject": subject,
        "severity": severity, "payload": json.dumps(redact(payload), default=str)[:8000] if payload is not None else None,
    }
    with db() as conn:
        conn.execute(
            "INSERT INTO events(ts,kind,source,subject,severity,payload) VALUES(?,?,?,?,?,?)",
            (row["ts"], row["kind"], row["source"], row["subject"], row["severity"], row["payload"]),
        )
    return row


def run(cmd, timeout=45, cwd=None, input_text=None, env=None) -> dict:
    """Run a command and return a redacted, trimmed result."""
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, shell=isinstance(cmd, str), cwd=cwd, input=input_text,
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return {
            "cmd": cmd if isinstance(cmd, str) else " ".join(cmd),
            "exit_code": proc.returncode,
            "stdout": redact_text(proc.stdout[-MAX_OUT:]),
            "stderr": redact_text(proc.stderr[-MAX_OUT:]),
            "elapsed": round(time.time() - started, 2),
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd if isinstance(cmd, str) else " ".join(cmd),
                "error": f"timed out after {timeout}s"}
    except Exception as exc:  # noqa: BLE001
        return {"cmd": str(cmd), "error": f"{type(exc).__name__}: {exc}"}


def need(binary: str):
    if not shutil.which(binary):
        raise RuntimeError(f"required binary not installed: {binary}")
    return binary


def audit(tool: str, args, result=None, *, goal="", verification="", files=None,
          commands=None, rollback="", actor="genie") -> dict:
    entry = {
        "ts": time.time(), "actor": actor, "goal": goal, "tool": tool,
        "args": json.dumps(redact(args), default=str)[:4000],
        "result": json.dumps(redact(result), default=str)[:4000] if result is not None else None,
        "verification": verification,
        "files": json.dumps(files or []), "commands": json.dumps(commands or []),
        "rollback": rollback,
    }
    with db() as conn:
        conn.execute(
            "INSERT INTO audit(ts,actor,goal,tool,args,result,verification,files,commands,rollback)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (entry["ts"], entry["actor"], entry["goal"], entry["tool"], entry["args"],
             entry["result"], entry["verification"], entry["files"], entry["commands"], entry["rollback"]),
        )
    return entry


# --------------------------------------------------------------------------
# 1. window / application manager
# --------------------------------------------------------------------------
def _display() -> str:
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    for n in range(10):
        if Path(f"/tmp/.X11-unix/X{n}").exists():
            os.environ["DISPLAY"] = f":{n}"
            return os.environ["DISPLAY"]
    raise RuntimeError("No X11 DISPLAY found.")


def _window_list() -> list[dict]:
    need("wmctrl")
    out = run(["wmctrl", "-l", "-G"], timeout=20)
    windows = []
    for line in out.get("stdout", "").splitlines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        wid, desk, gx, gy, gw, gh, title = parts
        try:
            gx, gy, gw, gh = int(gx), int(gy), int(gw), int(gh)
        except ValueError:
            gx = gy = gw = gh = 0
        windows.append({"id": wid, "desktop": desk, "geometry": [gx, gy, gw, gh], "title": title})
    active = run(["xdotool", "getactivewindow"], timeout=10)
    active_id = active.get("stdout", "").strip()
    for w in windows:
        w["active"] = (w["id"] == active_id)
    return windows


def _resolve_window(win) -> str:
    """Accept an id (0x..), a title substring, or 'active'."""
    if win in (None, "", "active"):
        out = run(["xdotool", "getactivewindow"], timeout=10).get("stdout", "").strip()
        if not out:
            raise RuntimeError("no active window")
        return out
    win = str(win)
    if win in ("0x", ""):
        raise RuntimeError("no active window")
    if win.isdigit() or win.startswith("0x"):
        return win
    for w in _window_list():
        if win.lower() in w["title"].lower():
            return w["id"]
    raise RuntimeError(f"no window matching: {win}")


def window_tool(action, **kw):
    _display()
    need("xdotool")
    if action == "list":
        return {"ok": True, "display": os.environ["DISPLAY"], "count": len(_window_list()),
                "windows": _window_list()}
    wid = _resolve_window(kw.get("window"))
    if action == "active":
        name = run(["xdotool", "getwindowname", wid], timeout=10)
        geo = run(["xdotool", "getwindowgeometry", wid], timeout=10)
        return {"ok": True, "id": wid, "name": name.get("stdout", "").strip(),
                "geometry": geo.get("stdout", "").strip()}
    if action in ("focus", "activate"):
        r = run(["wmctrl", "-ia", wid], timeout=15)
    elif action == "minimize":
        r = run(["xdotool", "windowminimize", wid], timeout=15)
    elif action == "maximize":
        r = run(["wmctrl", "-ir", wid, "-b", "add,maximized_vert,maximized_horz"], timeout=15)
    elif action in ("restore", "unmaximize"):
        r = run(["wmctrl", "-ir", wid, "-b", "remove,maximized_vert,maximized_horz"], timeout=15)
    elif action == "close":
        r = run(["wmctrl", "-ic", wid], timeout=15)
    elif action == "kill":
        r = run(["xdotool", "windowkill", wid], timeout=15)
    elif action == "geometry":
        return {"ok": True, "id": wid,
                "geometry": run(["xdotool", "getwindowgeometry", wid], timeout=10).get("stdout", "").strip()}
    elif action == "move":
        r = run(["wmctrl", "-ir", wid, "-e", "0," + ",".join([
            str(int(kw.get("x", 0))), str(int(kw.get("y", 0))), "-1", "-1"])], timeout=15)
    elif action == "resize":
        r = run(["wmctrl", "-ir", wid, "-e", "0,-1,-1," + ",".join(
            str(int(kw.get(k, 0))) for k in ("w", "h"))], timeout=15)
    elif action == "sendkey":
        r = run(["xdotool", "key", "--window", wid, str(kw.get("key"))], timeout=15)
    elif action == "type":
        r = run(["xdotool", "type", "--window", wid, "--clearmodifiers", str(kw.get("text", ""))], timeout=30)
    elif action == "launch":
        cmd = str(kw.get("command", ""))
        if not cmd:
            raise RuntimeError("command is required to launch an application")
        r = run(f"nohup setsid {cmd} >/tmp/genie-launch.log 2>&1 &", timeout=10)
        r = {"exit_code": 0, "stdout": "launched", "stderr": ""}
    else:
        raise ValueError(f"unknown window action: {action}")
    audit("window", {"action": action, **kw}, r)
    return {"ok": r.get("exit_code", 1) == 0, "action": action, "id": wid, **r}


# --------------------------------------------------------------------------
# 2. service manager
# --------------------------------------------------------------------------
def _systemctl(args, timeout=45, journal=False):
    scope = kw_scope = None
    if kw_scope:
        args = [f"--user{args}"] + list(args) if False else args
    return args


def service_tool(action, name="", **kw):
    need("systemctl")
    scope = kw.get("scope", "user")
    flag = ["--user"] if scope == "user" else []

    if action == "list":
        if scope == "user":
            r = run(["systemctl", *flag, "list-units", "--type=service", "--no-pager",
                     "--no-legend", "--plain"], timeout=30)
        else:
            r = run(["systemctl", "list-units", "--type=service", "--no-pager",
                     "--no-legend", "--plain"], timeout=30)
        lines = [l for l in r.get("stdout", "").splitlines() if l.strip()]
        return {"ok": r.get("exit_code") == 0, "scope": scope, "count": len(lines), "units": lines[:200],
                "error": r.get("stderr", "").strip()[-2000:] if r.get("exit_code") != 0 else ""}

    if not name:
        raise RuntimeError("service name is required")
    unit = name if name.endswith(".service") else f"{name}.service"

    if action == "status":
        r = run(["systemctl", *flag, "status", unit, "--no-pager"], timeout=30)
        props = run(["systemctl", *flag, "show", unit,
                     "-p", "ActiveState", "-p", "SubState", "-p", "NRestarts",
                     "-p", "Result", "-p", "ExecMainStatus", "-p", "ActiveEnterTimestamp"],
                    timeout=30)
        return {"ok": True, "unit": unit, "status": r.get("stdout", "")[-8000:],
                "properties": props.get("stdout", "")}
    if action in ("start", "stop", "restart", "reload", "try-restart"):
        r = run(["systemctl", *flag, action, unit, "--no-pager"], timeout=90)
        audit("service", {"action": action, "unit": unit, "scope": scope}, r,
              verification=run(["systemctl", *flag, "is-active", unit], timeout=20).get("stdout", "").strip())
        emit("service.changed", "service", unit, "info", {"action": action, "exit": r.get("exit_code")})
        return {"ok": r.get("exit_code") == 0, "unit": unit, "action": action, **r}
    if action in ("enable", "disable"):
        r = run(["systemctl", *flag, action, unit, "--no-pager"], timeout=60)
        audit("service", {"action": action, "unit": unit}, r)
        return {"ok": r.get("exit_code") == 0, "unit": unit, "action": action, **r}
    if action == "logs":
        lines = str(kw.get("lines", 100))
        r = run(["journalctl", *flag, "-u", unit, "-n", lines, "--no-pager"], timeout=30)
        return {"ok": True, "unit": unit, "lines": lines, "log": r.get("stdout", "")[-12000:]}
    if action == "health":
        return _service_health(unit, flag, kw)
    if action == "crashes":
        props = run(["systemctl", *flag, "show", unit, "-p", "NRestarts", "-p", "Result",
                     "-p", "ExecMainStatus", "-p", "ActiveState", "-p", "SubState"], timeout=20)
        values = dict(
            line.split("=", 1) for line in props.get("stdout", "").splitlines() if "=" in line)
        restarts = int(values.get("NRestarts", 0) or 0)
        return {"ok": True, "unit": unit, "restarts": restarts,
                "crash_loop": bool(values.get("Result") == "exit-code"
                                   or values.get("ActiveState") == "activating" and restarts > 3
                                   or restarts > 5),
                "properties": values}
    raise ValueError(f"unknown service action: {action}")


def _service_health(unit, flag, kw):
    active = run(["systemctl", *flag, "is-active", unit], timeout=20).get("stdout", "").strip()
    show = run(["systemctl", *flag, "show", unit, "-p", "ActiveState", "-p", "SubState",
                "-p", "NRestarts", "-p", "Result", "-p", "MainPID"], timeout=20).get("stdout", "")
    values = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    health = {"ok": active == "active", "unit": unit, "active": active, "properties": values}
    url = kw.get("url")
    if url:
        r = run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
                 "--max-time", str(kw.get("timeout", 10)), url], timeout=30)
        code = r.get("stdout", "").split()[0] if r.get("stdout") else "000"
        health["http"] = {"url": url, "status": code, "ok": code.startswith("2"),
                          "detail": r.get("stdout", "").strip()}
        health["ok"] = health["ok"] and health["http"]["ok"]
    return health


# --------------------------------------------------------------------------
# 3. network diagnostics
# --------------------------------------------------------------------------
def netdiag_tool(action, target="", **kw):
    need("curl")
    if action == "interfaces":
        r = run(["ip", "-brief", "address"], timeout=20)
        return {"ok": r.get("exit_code") == 0, "interfaces": r.get("stdout", "").strip()}
    if action == "routes":
        r = run(["ip", "route"], timeout=20)
        return {"ok": True, "routes": r.get("stdout", "").strip()}
    if action == "listeners":
        r = run(["ss", "-tulpn"], timeout=20)
        return {"ok": True, "listeners": r.get("stdout", "")[-12000:]}
    if action == "tailscale":
        r = run(["tailscale", "status", "--json"], timeout=25)
        if r.get("exit_code") != 0:
            return {"ok": False, "error": r.get("stderr") or r.get("stdout")}
        try:
            data = json.loads(r.get("stdout", "{}"))
        except Exception:
            return {"ok": False, "raw": r.get("stdout", "")[:4000]}
        peers = data.get("Peer") or {}
        return {"ok": True, "self": {k: data.get(k) for k in
                                      ("BackendState", "TailscaleIPs", "HostName", "Online")},
                "peers": [{"host": p.get("HostName"), "ips": p.get("TailscaleIPs"),
                           "online": p.get("Online"), "os": p.get("OS")} for p in list(peers.values())[:40]]}
    if not target and action not in ("doctor",):
        raise RuntimeError("target is required (host, ip, url or host:port)")

    if action == "dns":
        host = target.split("://")[-1].split("/")[0].split(":")[0]
        r = run(["dig", "+short", host], timeout=20)
        return {"ok": r.get("exit_code") == 0, "host": host,
                "records": [l for l in r.get("stdout", "").splitlines() if l.strip()]}
    if action == "ping":
        count = str(kw.get("count", 4))
        r = run(["ping", "-c", count, "-W", str(kw.get("wait", 2)), target], timeout=40)
        text = r.get("stdout", "") + r.get("stderr", "")
        loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", text)
        rtt = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", text)
        return {"ok": r.get("exit_code") == 0, "target": target,
                "loss_pct": float(loss.group(1)) if loss else None,
                "avg_ms": float(rtt.group(2)) if rtt else None, "raw": text[-2000:]}
    if action == "tcp":
        host, _, port = target.rpartition(":")
        r = run(["bash", "-lc", f"timeout 5 bash -c '</dev/tcp/{host}/{port}'"], timeout=15)
        return {"ok": r.get("exit_code") == 0, "host": host, "port": port,
                "detail": (r.get("stdout") or r.get("stderr") or "").strip()[:300]}
    if action == "http":
        r = run(["curl", "-sS", "-o", "/dev/null", "-w",
                 "%{http_code} %{time_total} %{url_effective}", "--max-time",
                 str(kw.get("timeout", 15)), target], timeout=30)
        parts = (r.get("stdout") or "").split()
        return {"ok": bool(parts) and parts[0].startswith("2"),
                "url": target, "status": parts[0] if parts else "000",
                "seconds": parts[1] if len(parts) > 1 else None, "detail": r.get("stderr", "")[:300]}
    if action == "ssh":
        r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", target, "echo ok"],
                timeout=20)
        return {"ok": r.get("exit_code") == 0, "target": target,
                "detail": (r.get("stdout") or r.get("stderr") or "").strip()[:400]}
    if action == "doctor":
        return netdiag_doctor(kw)
    raise ValueError(f"unknown netdiag action: {action}")


def netdiag_doctor(kw=None) -> dict:
    """Classify a connectivity problem down to the failing layer."""
    kw = kw or {}
    target = str(kw.get("target", "") or "")
    layers = {}
    layers["interface"] = run(["ip", "-brief", "address"], timeout=15).get("stdout", "").strip()
    layers["default_route"] = run(["ip", "route", "show", "default"], timeout=15).get("stdout", "").strip()
    if target:
        host = target.split("://")[-1].split("/")[0].split(":")[0]
        dns = netdiag_tool("dns", host)
        layers["dns"] = dns
        if not dns.get("records"):
            verdict = "DNS failure"
        else:
            ip = dns["records"][0]
            layers["ping"] = netdiag_tool("ping", ip)
            if not layers["ping"].get("ok"):
                verdict = "network reachability failure (ICMP blocked or host down)"
            elif ":" in target or target.startswith("http"):
                http = netdiag_tool("http", target if target.startswith("http") else f"http://{target}")
                layers["http"] = http
                verdict = "network OK, application layer failing" if not http.get("ok") else "all layers healthy"
            else:
                verdict = "all layers healthy"
    else:
        verdict = "no target supplied; interface and route only"
    return {"verdict": verdict, "layers": layers}
def android_tool(action, serial="", **kw):
    need("adb")
    if action == "devices":
        r = run(["adb", "devices", "-l"], timeout=20)
        return {"ok": r.get("exit_code")==0, "devices": r.get("stdout","")}
    if action == "select":
        serial = serial or (run(["adb","get-serialno"], timeout=15).get("stdout","").strip())
        return {"ok": serial not in ("","unknown"), "serial": serial}
    cmd = ["adb"] + (["-s", serial] if serial else [])
    if action == "shell":
        command = kw.get("command","")
        return run(cmd + ["shell", "sh", "-c", command], timeout=int(kw.get("timeout",60)))
    if action == "screenshot":
        out = Path(kw.get("path") or (SHOTS/f"android-{int(time.time())}.png"))
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = subprocess.run(cmd + ["exec-out","screencap","-p"], capture_output=True, timeout=30)
        if raw.returncode != 0:
            return {"ok":False, "stderr":raw.stderr.decode(errors="replace")}
        out.write_bytes(raw.stdout)
        return {"ok":True, "path":str(out), "bytes":out.stat().st_size}
    if action in ("push","pull"):
        src,dst = str(kw["src"]),str(kw["dst"])
        r = run(cmd + [action,src,dst], timeout=int(kw.get("timeout",120)))
        return {"ok":r.get("exit_code")==0, **r}
    if action == "input":
        kind = kw.get("kind","text")
        value = str(kw.get("value",""))
        if kind == "tap": args=["shell","input","tap",str(kw["x"]),str(kw["y"])]
        elif kind == "keyevent": args=["shell","input","keyevent",value]
        elif kind == "text": args=["shell","input","text",shlex.quote(value)]
        else: raise ValueError("input kind must be tap, keyevent, or text")
        r = run(cmd+args, timeout=20); return {"ok":r.get("exit_code")==0, **r}
    if action == "reboot":
        r = run(cmd+["reboot"], timeout=20); return {"ok":r.get("exit_code")==0, **r}
    raise ValueError(f"unknown android action: {action}")

def event_tool(action, kind="", payload=None, limit=50):
    if action == "emit":
        row = emit(kind or "event", "genie", str((payload or {}).get("subject","")),
                   str((payload or {}).get("severity","info")), payload or {})
        return {"ok":True, "event":row}
    if action in ("recent","query"):
        with db() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return {"ok":True, "events":[dict(r) for r in rows]}
    raise ValueError("event action must be emit, recent, or query")

def memory_tool(action, namespace="default", key="", value=None, tags=None, limit=50):
    with db() as c:
        if action == "set":
            c.execute("INSERT OR REPLACE INTO kv(ns,key,value,tags,updated) VALUES(?,?,?,?,?)",
                      (namespace,key,json.dumps(value,default=str),json.dumps(tags or []),time.time()))
            return {"ok":True,"namespace":namespace,"key":key}
        if action == "get":
            r=c.execute("SELECT * FROM kv WHERE ns=? AND key=?",(namespace,key)).fetchone()
            return {"ok":bool(r),"value":json.loads(r["value"]) if r else None,
                    "tags":json.loads(r["tags"]) if r else []}
        if action == "search":
            rows=c.execute("SELECT * FROM kv WHERE ns=? ORDER BY updated DESC LIMIT ?",
                           (namespace,int(limit))).fetchall()
            return {"ok":True,"items":[dict(r) for r in rows]}
        if action == "delete":
            cur = c.execute("DELETE FROM kv WHERE ns=? AND key=?",(namespace,key))
            return {"ok":True,"deleted":cur.rowcount}
    raise ValueError("memory action must be set, get, search, or delete")
def symbol_tool(action, path="", query="", max_results=500):
    root = Path(os.path.expanduser(path or ".")).resolve()
    index_file = STATE/"symbols.json"
    if action == "build":
        items=[]
        for p in root.rglob("*"):
            if not p.is_file() or any(x in p.parts for x in (".git",".venv","node_modules","__pycache__")): continue
            if p.suffix == ".py":
                try:
                    import ast
                    tree=ast.parse(p.read_text(errors="replace"))
                    for n in ast.walk(tree):
                        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                            items.append({"name":n.name,"kind":type(n).__name__,
                                          "path":str(p),"line":n.lineno})
                except Exception: pass
            elif p.suffix in (".js",".jsx",".ts",".tsx",".go",".rs"):
                try:
                    for i,line in enumerate(p.read_text(errors="replace").splitlines(),1):
                        m=re.search(r"\b(?:function|class|def|fn|func|type)\s+([A-Za-z_$][\w$]*)",line)
                        if m: items.append({"name":m.group(1),"kind":"symbol","path":str(p),"line":i})
                except Exception: pass
            if len(items)>=int(max_results)*4: break
        index_file.write_text(json.dumps({"root":str(root),"built":time.time(),"symbols":items},indent=2))
        return {"ok":True,"root":str(root),"count":len(items),"index":str(index_file)}
    if not index_file.exists(): return symbol_tool("build",str(root),max_results=max_results)
    data=json.loads(index_file.read_text())
    items=data.get("symbols",[])
    if query: items=[x for x in items if query.lower() in x["name"].lower()]
    return {"ok":True,"count":len(items[:int(max_results)]),"items":items[:int(max_results)],
            "index":str(index_file)}
def route_tool(action, goal="", role="", max_steps=8):
    roles = {
        "data":["data","csv","metric","analytics","kpi"],
        "filesystem":["file","folder","disk","filesystem"],
        "web":["web","search","research","url","internet"],
        "media":["image","video","audio","photo"],
        "visual":["screen","ui","visual","desktop"],
        "coding":["code","bug","build","program","repo"],
        "qa":["test","qa","verify","regression"],
        "integration":["integrate","connect","api","service"],
    }
    if action == "plan":
        t=goal.lower()
        scores={k:sum(1 for w in ws if re.search(r"\b"+re.escape(w)+r"\b",t)) for k,ws in roles.items()}
        return {"ok":True,"goal":goal,"ranking":sorted(scores,key=scores.get,reverse=True),
                "scores":scores}
    if action == "run":
        if not goal: raise ValueError("goal is required")
        if not role:
            role=route_tool("plan",goal)["ranking"][0]
        root=Path.home()/"workspace/projects/active/agent-team"
        prompt=f"[GENIE route:{role}] {goal}"
        p=subprocess.run(["make",f"GOAL={prompt}","genie"],cwd=str(root),
                         capture_output=True,text=True,timeout=900)
        return {"ok":p.returncode==0,"role":role,"exit_code":p.returncode,
                "stdout":redact_text(p.stdout[-MAX_OUT:]),"stderr":redact_text(p.stderr[-8000:])}
    raise ValueError("route action must be plan or run")

def model_tool(action, prompt="", required=None, max_attempts=6):
    key=next((v for v in (Path(ROOT/"openrouter.env").read_text(errors="replace").splitlines())
              if v.startswith("OPENROUTER_API_KEY=")), "")
    key=key.split("=",1)[1].strip().strip("\\'").strip('"') if key else ""
    if not key: raise RuntimeError("OpenRouter key missing")
    import urllib.request as _ur
    req=_ur.Request("https://openrouter.ai/api/v1/models?max_price=0&output_modalities=text",
                    headers={"Authorization":f"Bearer {key}"})
    data=json.loads(_ur.urlopen(req,timeout=30).read().decode())
    models=data.get("data",[])
    if action in ("plan","rank"):
        required=set(required or [])
        def score(m):
            pars=set(m.get("supported_parameters") or [])
            arch=m.get("architecture") or {}
            s=(1000 if ("tools" in required and "tools" in pars) else 0)
            s+=(350 if ("reasoning" in required and ("reasoning" in pars or "include_reasoning" in pars)) else 0)
            mods=set(arch.get("input_modalities") or m.get("input_modalities") or [])
            s+=(400 if ("vision" in required and "image" in mods) else 0)
            s+=min(int(m.get("context_length") or 0)//1000,1000)
            return s
        ranked=sorted(models,key=score,reverse=True)
        return {"ok":True,"count":len(ranked),"models":[{"id":m["id"],"score":score(m),
                "context":m.get("context_length")} for m in ranked[:max_attempts]]}
    if action == "run":
        if not prompt: raise ValueError("prompt is required")
        ranked=model_tool("plan",required=required,max_attempts=max_attempts)["models"]
        attempts=[]
        for m in ranked:
            try:
                body={"model":m["id"],"messages":[{"role":"user","content":prompt}],
                      "max_tokens":3000,"temperature":0.2}
                rq=_ur.Request("https://openrouter.ai/api/v1/chat/completions",
                    data=json.dumps(body).encode(),headers={"Authorization":f"Bearer {key}",
                    "Content-Type":"application/json"})
                resp=json.loads(_ur.urlopen(rq,timeout=120).read().decode())
                text=(resp.get("choices") or [{}])[0].get("message",{}).get("content","")
                if text: return {"ok":True,"model":m["id"],"attempts":attempts+[m["id"]],"text":text}
            except Exception as e:
                attempts.append(m["id"]+":"+type(e).__name__)
        return {"ok":False,"attempts":attempts}
    raise ValueError("model action must be plan, rank, or run")
def github_tool(action, repo="", **kw):
    need("gh")
    repo=repo or kw.get("repository") or ""
    base=["gh"]
    def api(path, extra=None):
        cmd=base+["api",path]
        for a in (extra or []): cmd.append(a)
        return run(cmd,timeout=60)
    if action == "auth":
        r=run(["gh","auth","status"],timeout=30); return {"ok":r.get("exit_code")==0,**r}
    if action == "repo":
        if not repo: return {"ok":False,"error":"repo owner/name required"}
        return api(f"repos/{repo}")
    if action == "issues":
        return api(f"repos/{repo}/issues?state={kw.get('state','open')}&per_page={int(kw.get('limit',20))}")
    if action == "prs":
        return api(f"repos/{repo}/pulls?state={kw.get('state','open')}&per_page={int(kw.get('limit',20))}")
    if action == "runs":
        return api(f"repos/{repo}/actions/runs?per_page={int(kw.get('limit',20))}")
    if action == "issue_comment":
        cmd=["gh","issue","comment",str(kw["number"]),"--repo",repo,"--body",str(kw["body"])]
        r=run(cmd,timeout=60); return {"ok":r.get("exit_code")==0,**r}
    if action == "pr_comment":
        cmd=["gh","pr","comment",str(kw["number"]),"--repo",repo,"--body",str(kw["body"])]
        r=run(cmd,timeout=60); return {"ok":r.get("exit_code")==0,**r}
    if action == "issue_create":
        cmd=["gh","issue","create","--repo",repo,"--title",str(kw["title"]),"--body",str(kw.get("body",""))]
        r=run(cmd,timeout=60); return {"ok":r.get("exit_code")==0,**r}
    if action == "pr_create":
        cmd=["gh","pr","create","--repo",repo,"--title",str(kw["title"]),"--body",str(kw.get("body","")),
             "--base",str(kw.get("base","main")),"--head",str(kw["head"])]
        r=run(cmd,timeout=60); return {"ok":r.get("exit_code")==0,**r}
    raise ValueError(f"unknown github action: {action}")

_browser_profiles = {}
_browser_logs = {"console":[],"requests":[],"responses":[]}

def browser_inspect_tool(action="logs", limit=100):
    import importlib
    ge=importlib.import_module("genie_ext")
    page=getattr(ge,"_page",None)
    if page is None or page.is_closed(): return {"ok":False,"error":"GENIE browser page is not active"}
    if action == "attach":
        def c(msg): _browser_logs["console"].append({"type":msg.type,"text":msg.text})
        def req(req): _browser_logs["requests"].append({"method":req.method,"url":req.url,"resource":req.resource_type})
        def resp(resp): _browser_logs["responses"].append({"status":resp.status,"url":resp.url})
        page.on("console",c); page.on("request",req); page.on("response",resp)
        return {"ok":True,"attached":True}
    if action == "logs":
        return {"ok":True,"console":_browser_logs["console"][-limit:],
                "requests":_browser_logs["requests"][-limit:],
                "responses":_browser_logs["responses"][-limit:]}
    if action == "clear":
        for k in _browser_logs: _browser_logs[k].clear()
        return {"ok":True}
    raise ValueError("browser_inspect action must be attach, logs, or clear")

def browser_profile_tool(action, name="default", **kw):
    import importlib
    global _browser_profiles
    if action == "list":
        profiles = sorted(_browser_profiles)
        if (ROOT / "browser-profile").exists() and "default" not in profiles:
            profiles.insert(0, "default")
        return {"ok":True,"profiles":profiles}
    if action == "open":
        if name in _browser_profiles:
            return {"ok":True,"name":name,"url":_browser_profiles[name]["page"].url}
        ge=importlib.import_module("genie_ext")
        pw=getattr(ge,"_pw",None)
        own_pw=False
        if pw is None:
            from playwright.sync_api import sync_playwright
            pw=sync_playwright().start()
            own_pw=True
        pdir=ROOT/"profiles"/re.sub(r"[^A-Za-z0-9_.-]","-",name)
        pdir.mkdir(parents=True,exist_ok=True)
        ctx=pw.chromium.launch_persistent_context(
            str(pdir),headless=bool(kw.get("headless",False)),
            executable_path=kw.get("executable","/usr/bin/google-chrome"),
            accept_downloads=True)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        _browser_profiles[name]={"pw":pw,"own_pw":own_pw,"ctx":ctx,"page":page}
        return {"ok":True,"name":name,"url":page.url,"profile":str(pdir)}
    if name not in _browser_profiles: raise ValueError(f"profile not open: {name}")
    rec=_browser_profiles[name]; page=rec["page"]
    if action == "navigate":
        r=page.goto(kw["url"],wait_until="domcontentloaded",timeout=int(kw.get("timeout_ms",60000)))
        return {"ok":True,"name":name,"url":page.url,"title":page.title(),"status":r.status if r else None}
    if action == "snapshot":
        return {"ok":True,"name":name,"url":page.url,"title":page.title(),
                "text":page.locator("body").inner_text()[:int(kw.get("max_chars",20000))]}
    if action == "close":
        rec["ctx"].close()
        if rec.get("own_pw"): rec["pw"].stop()
        del _browser_profiles[name]
        return {"ok":True,"closed":name}
    raise ValueError("browser_profile action must be list, open, navigate, snapshot, or close")
def build_repair_tool(action="diagnose", cwd=None, manager="auto", auto_fix=False, command=None):
    cwd=str(Path(os.path.expanduser(cwd or Path.cwd())).resolve())
    if manager=="auto":
        p=Path(cwd)
        manager="npm" if (p/"package.json").exists() else ("cargo" if (p/"Cargo.toml").exists() else "python")
    if command: cmd=command
    elif manager=="npm": cmd="npm test"
    elif manager=="cargo": cmd="cargo test"
    else: cmd="pytest -q"
    r=run(["/bin/bash","-lc",cmd],cwd=cwd,timeout=900)
    combined=(r.get("stdout","")+chr(10)+r.get("stderr",""))
    failures=[]
    pats=[r"(?P<path>[\w./-]+\.py):(?P<line>\d+)",r"(?P<path>[\w./-]+\.(?:js|ts|tsx|jsx)):.*?(?P<line>\d+)",
          r"(?P<path>[\w./-]+):(?P<line>\d+):\d+"]
    for pat in pats:
        for m in re.finditer(pat,combined):
            failures.append({"path":m.group("path"),"line":int(m.group("line"))})
    result={"ok":r.get("exit_code")==0,"manager":manager,"command":cmd,
            "exit_code":r.get("exit_code"),"failures":failures[:100],"output":combined[-20000:]}
    if result["ok"] or not auto_fix: return result
    try:
        import importlib
        g=importlib.import_module("genie")
        prompt=(f"Repair this failing project in {cwd}. Test command: {cmd}. "
                f"Failure output:\n{combined[-12000:]}\nInspect, make minimal safe fixes, rerun tests, and report changes.")
        result["repair"] = g.run_agent(prompt)
    except Exception as e: result["repair_error"]=f"{type(e).__name__}: {e}"
    return result
def security_scan_tool(path=None, manager="auto"):
    root=Path(os.path.expanduser(path or ".")).resolve()
    if manager=="auto":
        manager="npm" if (root/"package.json").exists() else ("cargo" if (root/"Cargo.toml").exists() else "python")
    checks=[]
    if manager=="npm":
        if (root/"package-lock.json").exists() or (root/"npm-shrinkwrap.json").exists():
            r=run(["npm","audit","--json"],cwd=str(root),timeout=900); checks.append({"tool":"npm audit","available":True,"result":r})
        else: checks.append({"tool":"npm audit","available":True,"note":"lockfile not present"})
    elif manager=="cargo":
        if shutil.which("cargo-audit"):
            checks.append({"tool":"cargo audit","available":True,"result":run(["cargo","audit","--json"],cwd=str(root),timeout=900)})
        else: checks.append({"tool":"cargo audit","available":False,"note":"cargo-audit not installed"})
    else:
        if shutil.which("pip-audit"):
            checks.append({"tool":"pip-audit","available":True,"result":run(["pip-audit","-f","json"],cwd=str(root),timeout=900)})
        else: checks.append({"tool":"pip-audit","available":False,"note":"pip-audit not installed"})
    secret=run(["rg","-n","--hidden","--glob","!.git","--glob","!.venv/**","--glob","!node_modules/**","--glob","!browser-profile/**","(?i)(api[_-]?key|secret|password|token)\\s*[:=]\\s*['\"]?[A-Za-z0-9_./+=-]{12,}",
                 str(root)],timeout=120)
    possible = [redact_text(x) for x in secret.get("stdout","").splitlines() if x][:100]
    return {"ok":True,"root":str(root),"manager":manager,"checks":redact(checks),
            "possible_secrets":possible}
def snapshot_tool(action, path=None, snapshot=None, target=None, name=None):
    import tarfile, datetime
    snapdir=STATE/"snapshots"; snapdir.mkdir(parents=True,exist_ok=True)
    if action=="create":
        root=Path(os.path.expanduser(path or ".")).resolve()
        if not root.exists(): raise FileNotFoundError(root)
        name=name or f"{root.name}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        out=snapdir/(re.sub(r"[^A-Za-z0-9_.-]","-",name)+".tar.gz")
        with tarfile.open(out,"w:gz") as tar:
            tar.add(root,arcname=root.name,filter=lambda ti: None if any(x in ti.name.split("/") for x in (".git",".venv","node_modules","__pycache__")) else ti)
        return {"ok":True,"snapshot":str(out),"root":str(root),"bytes":out.stat().st_size}
    if action=="list":
        return {"ok":True,"snapshots":[str(p) for p in sorted(snapdir.glob("*.tar.gz"))]}
    if action=="rollback":
        if not snapshot or not target: raise ValueError("snapshot and target are required")
        snap=Path(snapshot).resolve(); dest=Path(os.path.expanduser(target)).resolve()
        if not snap.exists(): raise FileNotFoundError(snap)
        pre=snapshot_tool("create",str(dest),name=f"pre-rollback-{dest.name}")
        with tarfile.open(snap,"r:gz") as tar:
            members=tar.getmembers()
            prefix=Path(members[0].name).parts[0] if members else ""
            tar.extractall(dest.parent,filter="data")
        return {"ok":True,"rolled_back":str(dest),"pre_rollback":pre,"source":str(snap)}
    raise ValueError("snapshot action must be create, list, or rollback")
def watchdog_tool(action, services=None, interval=15, max_restarts=3, pid=None):
    state=STATE/"watchdogs.json"
    data=json.loads(state.read_text()) if state.exists() else {}
    if action=="list": return {"ok":True,"watchdogs":data}
    if action=="start":
        services=services or []
        if not services: raise ValueError("services required")
        script=("import time,subprocess,json,os\n"
                f"services={json.dumps(list(services))}\n"
                f"interval={int(interval)}\n"
                f"limit={int(max_restarts)}\n"
                f"state={json.dumps(str(STATE/'watchdog-events.json'))}\n"
                "counts={}\nwhile True:\n"
                "  for u in services:\n"
                "    p=subprocess.run(['systemctl','--user','is-active',u+'.service'],capture_output=True,text=True)\n"
                "    if p.returncode!=0:\n"
                "      c=counts.get(u,0)\n"
                "      if c<limit:\n"
                "        subprocess.run(['systemctl','--user','restart',u+'.service'])\n"
                "        counts[u]=c+1\n"
                "        open(state,'a').write(json.dumps({'ts':time.time(),'service':u,'action':'restart','count':c+1})+'\n')\n"
                "  time.sleep(interval)\n")
        p=subprocess.Popen(["python3","-c",script],start_new_session=True)
        data[str(p.pid)]={"pid":p.pid,"services":list(services),"interval":interval,"max_restarts":max_restarts,"started":time.time()}
        state.write_text(json.dumps(data,indent=2))
        return {"ok":True,"pid":p.pid,"services":services}
    if action=="stop":
        pid=int(pid or (services if isinstance(services,int) else 0))
        if not pid: raise ValueError("watchdog pid required in services")
        try: os.kill(pid,15)
        except ProcessLookupError: pass
        data.pop(str(pid),None); state.write_text(json.dumps(data,indent=2))
        return {"ok":True,"pid":pid,"stopped":True}
    raise ValueError("watchdog action must be start, stop, or list")
def watch_tool(action, path=None, pattern="*", interval=2, watch_id=""):
    state=STATE/"watchers.json"
    data=json.loads(state.read_text()) if state.exists() else {}
    if action=="list": return {"ok":True,"watchers":data}
    if action=="start":
        root=str(Path(os.path.expanduser(path or ".")).resolve())
        script=("import os,time,fnmatch,json\n"
                f"root={json.dumps(root)}; pattern={json.dumps(pattern)}; interval={float(interval)}; out={json.dumps(str(STATE/'watch-events.json'))}\n"
                "old={}\n"
                "while True:\n"
                " new={}\n"
                " for dp,ds,fs in os.walk(root):\n"
                "  if any(x in dp.split(os.sep) for x in ['.git','node_modules','.venv','__pycache__']): continue\n"
                "  for f in fs:\n"
                "   p=os.path.join(dp,f)\n"
                "   if fnmatch.fnmatch(f,pattern):\n"
                "    try: new[p]=os.stat(p).st_mtime_ns\n"
                "    except: pass\n"
                " for p,t in new.items():\n"
                "  if p not in old: open(out,'a').write(json.dumps({'ts':time.time(),'event':'created','path':p})+'\\n')\n"
                "  elif old[p]!=t: open(out,'a').write(json.dumps({'ts':time.time(),'event':'modified','path':p})+'\\n')\n"
                " for p in set(old)-set(new): open(out,'a').write(json.dumps({'ts':time.time(),'event':'deleted','path':p})+'\\n')\n"
                " old=new; time.sleep(interval)\n")
        p=subprocess.Popen(["python3","-c",script],start_new_session=True)
        wid=watch_id or uuid.uuid4().hex[:12]
        data[wid]={"pid":p.pid,"path":root,"pattern":pattern,"interval":interval,"started":time.time()}
        state.write_text(json.dumps(data,indent=2))
        return {"ok":True,"watch_id":wid,"pid":p.pid}
    if action=="events":
        f=STATE/"watch-events.json"
        lines=f.read_text(errors="replace").splitlines()[-100:] if f.exists() else []
        return {"ok":True,"events":[json.loads(x) for x in lines if x]}
    if action=="stop":
        if not watch_id: raise ValueError("watch_id required")
        rec=data.pop(watch_id,None)
        if rec:
            try: os.kill(int(rec["pid"]),15)
            except ProcessLookupError: pass
            state.write_text(json.dumps(data,indent=2))
        return {"ok":True,"watch_id":watch_id,"stopped":bool(rec)}
    raise ValueError("watch action must be start, stop, list, or events")
def capabilities_tool(action="discover"):
    import importlib.util
    bins=["wmctrl","xdotool","systemctl","nmcli","ss","adb","gh","rg","curl","google-chrome","firefox"]
    mods=["playwright","requests","yaml","PIL","cryptography"]
    available={"binaries":[b for b in bins if shutil.which(b)],
               "missing_binaries":[b for b in bins if not shutil.which(b)],
               "python_modules":[m for m in mods if importlib.util.find_spec(m)],
               "missing_modules":[m for m in mods if not importlib.util.find_spec(m)],
               "display":os.environ.get("DISPLAY") or _display() if Path("/tmp/.X11-unix/X0").exists() else None}
    try:
        import genie
        available["genie_tools"]=[x["function"]["name"] for x in genie.TOOLS]
    except Exception as e: available["genie_tools_error"]=str(e)
    return {"ok":True,**available}
WORKSTATION_TOOLS = [
 {"type":"function","function":{"name":"window","description":"Window/application manager: list, focus, minimize, maximize, move, resize, type, keys, launch, close.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","active","focus","activate","minimize","maximize","restore","unmaximize","close","kill","geometry","move","resize","sendkey","type","launch"]},"window":{"type":"string"},"x":{"type":"integer"},"y":{"type":"integer"},"w":{"type":"integer"},"h":{"type":"integer"},"key":{"type":"string"},"text":{"type":"string"},"command":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"service","description":"Service manager with status, logs, restart, enable/disable, health, and crash-loop inspection.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","status","start","stop","restart","reload","try-restart","enable","disable","logs","health","crashes"]},"name":{"type":"string"},"scope":{"type":"string","enum":["user","system"]},"url":{"type":"string"},"lines":{"type":"integer"},"timeout":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"netdiag","description":"Network diagnostics: interfaces, routes, listeners, DNS, ping, TCP, HTTP, SSH, Tailscale, doctor.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["interfaces","routes","listeners","tailscale","dns","ping","tcp","http","ssh","doctor"]},"target":{"type":"string"},"count":{"type":"integer"},"wait":{"type":"integer"},"timeout":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"android","description":"ADB Android control: devices, shell, screenshots, push/pull, input, reboot.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["devices","select","shell","screenshot","push","pull","input","reboot"]},"serial":{"type":"string"},"command":{"type":"string"},"path":{"type":"string"},"src":{"type":"string"},"dst":{"type":"string"},"kind":{"type":"string","enum":["tap","keyevent","text"]},"x":{"type":"integer"},"y":{"type":"integer"},"value":{"type":"string"},"timeout":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"watch","description":"Persistent file watcher with create/modify/delete events.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","stop","list","events"]},"path":{"type":"string"},"pattern":{"type":"string"},"interval":{"type":"number"},"watch_id":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"event","description":"Persistent local event bus: emit and query structured events.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["emit","recent","query"]},"kind":{"type":"string"},"payload":{"type":"object"},"limit":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"watchdog","description":"Self-healing service watchdog with bounded automatic restart attempts.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","stop","list"]},"services":{"type":"array","items":{"type":"string"}},"interval":{"type":"integer"},"max_restarts":{"type":"integer"},"pid":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"memory","description":"Persistent GENIE memory/context database using SQLite namespaces, keys, values, and tags.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["set","get","search","delete"]},"namespace":{"type":"string"},"key":{"type":"string"},"value":{},"tags":{"type":"array"},"limit":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"symbol_index","description":"Build/search a persistent source-code symbol index for Python, JS/TS, Go, and Rust.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["build","search"]},"path":{"type":"string"},"query":{"type":"string"},"max_results":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"agent_route","description":"Route goals to specialist Agent Team roles or execute the routed specialist.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["plan","run"]},"goal":{"type":"string"},"role":{"type":"string"},"max_steps":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"model_route","description":"Rank free models or run a request with automatic fallback/escalation.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["plan","rank","run"]},"prompt":{"type":"string"},"required":{"type":"array"},"max_attempts":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"github","description":"Authenticated GitHub operations via gh: repos, issues, PRs, workflow runs, comments, creation.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["auth","repo","issues","prs","runs","issue_comment","pr_comment","issue_create","pr_create"]},"repo":{"type":"string"},"repository":{"type":"string"},"number":{"type":"integer"},"state":{"type":"string"},"limit":{"type":"integer"},"body":{"type":"string"},"title":{"type":"string"},"base":{"type":"string"},"head":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"browser_inspect","description":"Inspect GENIE browser console messages, network requests, and responses.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["attach","logs","clear"]},"limit":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"browser_profile","description":"Manage multiple isolated persistent Chrome profiles/sessions.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["list","open","navigate","snapshot","close"]},"name":{"type":"string"},"url":{"type":"string"},"headless":{"type":"boolean"},"max_chars":{"type":"integer"},"timeout_ms":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"web_search","description":"Dedicated free web-search tool returning structured result titles and URLs.","parameters":{"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer"}},"required":["query"]}}},
 {"type":"function","function":{"name":"build_repair","description":"Run project tests/builds, parse failures, and optionally hand failing work to GENIE for minimal repair and rerun.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["diagnose","repair"]},"cwd":{"type":"string"},"manager":{"type":"string","enum":["auto","python","npm","cargo"]},"auto_fix":{"type":"boolean"},"command":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"security_scan","description":"Run available dependency/security scanners and detect possible credential exposure patterns.","parameters":{"type":"object","properties":{"path":{"type":"string"},"manager":{"type":"string","enum":["auto","python","npm","cargo"]}}}}},
 {"type":"function","function":{"name":"snapshot","description":"Create, list, and rollback project snapshots; rollback records a pre-rollback snapshot first.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["create","list","rollback"]},"path":{"type":"string"},"snapshot":{"type":"string"},"target":{"type":"string"},"name":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"capabilities","description":"Dynamically discover installed binaries, Python modules, X11 state, and current GENIE tool registry.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["discover"]}}}}},
]

def execute_workstation_tool(name, args):
    if name=="window": return window_tool(args.get("action"), **{k:v for k,v in args.items() if k!="action"})
    if name=="service": return service_tool(args.get("action"),args.get("name",""),**{k:v for k,v in args.items() if k not in ("action","name")})
    if name=="netdiag": return netdiag_tool(args.get("action"),args.get("target",""),**{k:v for k,v in args.items() if k not in ("action","target")})
    if name=="android": return android_tool(args.get("action"),args.get("serial",""),**{k:v for k,v in args.items() if k not in ("action","serial")})
    if name=="watch": return watch_tool(**args)
    if name=="event": return event_tool(**args)
    if name=="watchdog": return watchdog_tool(args.get("action"),services=args.get("services"),interval=args.get("interval",15),max_restarts=args.get("max_restarts",3),pid=args.get("pid"))
    if name=="memory": return memory_tool(**args)
    if name=="symbol_index": return symbol_tool(args.get("action"),args.get("path",""),args.get("query",""),args.get("max_results",500))
    if name=="agent_route": return route_tool(**args)
    if name=="model_route": return model_tool(**args)
    if name=="github": return github_tool(**args)
    if name=="browser_inspect": return browser_inspect_tool(**args)
    if name=="browser_profile": return browser_profile_tool(**args)
    if name=="web_search": return web_search_tool(**args)
    if name=="build_repair": return build_repair_tool(**args)
    if name=="security_scan": return security_scan_tool(**args)
    if name=="snapshot": return snapshot_tool(**args)
    if name=="capabilities": return capabilities_tool(**args)
    return None
# Hardened web search uses the isolated headless browser for DOM-backed results.
# Robust web search with provider fallback and structured errors.
# Robust web search with provider fallback and structured errors.
# Robust web search with Bing primary and layered fallbacks.
def web_search_tool(query, limit=10):
    if not query:
        raise ValueError("query is required")
    errors = []
    import html
    import urllib.parse
    import requests

    try:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        raw = resp.text
        items = []
        seen = set()
        pattern = r'<li[^>]*class="b_algo"[^>]*>.*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</h2>'
        for m in re.finditer(pattern, raw, re.S | re.I):
            href = html.unescape(m.group(1)).replace("&amp;", "&")
            title = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            title = re.sub(r"\s+", " ", title).strip()
            if href in seen or len(title) < 2:
                continue
            seen.add(href)
            items.append({"title": title[:240], "url": href})
            if len(items) >= int(limit):
                break
        if items:
            return {"ok": True, "query": query, "count": len(items),
                    "results": items, "provider": "bing"}
        errors.append("Bing returned no parseable results")
    except Exception as exc:
        errors.append(f"Bing: {type(exc).__name__}: {exc}")

    try:
        url = "https://search.brave.com/search?" + urllib.parse.urlencode({"q": query})
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        raw = resp.text
        items = []
        seen = set()
        pattern = r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>'
        for m in re.finditer(pattern, raw, re.S | re.I):
            href = html.unescape(m.group(1)).replace("&amp;", "&")
            title = html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            title = re.sub(r"\s+", " ", title).strip()
            if href in seen or len(title) < 8:
                continue
            if href.startswith(("https://search.brave.com/", "https://cdn.search.brave.com/")):
                continue
            seen.add(href)
            items.append({"title": title[:240], "url": href})
            if len(items) >= int(limit):
                break
        if items:
            return {"ok": True, "query": query, "count": len(items),
                    "results": items, "provider": "brave"}
        errors.append("Brave returned no parseable results")
    except Exception as exc:
        errors.append(f"Brave: {type(exc).__name__}: {exc}")

    try:
        name = "__genie_search_fallback__"
        browser_profile_tool("open", name=name, headless=True)
        try:
            page = _browser_profiles[name]["page"]
            page.goto("https://www.bing.com/search?" + urllib.parse.urlencode({"q": query}),
                      wait_until="domcontentloaded", timeout=60000)
            rows = page.locator("li.b_algo h2 a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href||''}))")
            results = []
            seen = set()
            for row in rows:
                href = row["href"]
                title = row["text"].replace("\n", " ").strip()
                if href in seen or len(title) < 2:
                    continue
                seen.add(href)
                results.append({"title": title[:240], "url": href})
                if len(results) >= int(limit):
                    break
            if results:
                return {"ok": True, "query": query, "count": len(results),
                        "results": results, "provider": "bing-browser-fallback",
                        "fallback_from": errors}
            errors.append("browser Bing fallback returned no results")
        finally:
            browser_profile_tool("close", name=name)
    except Exception as exc:
        errors.append(f"Browser fallback: {type(exc).__name__}: {exc}")

    return {"ok": False, "query": query, "count": 0, "results": [], "errors": errors}
