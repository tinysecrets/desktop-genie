#!/usr/bin/env python3
"""GENIE capability extensions."""
import base64
import csv
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path
from genie_workstation import WORKSTATION_TOOLS, execute_workstation_tool

ROOT = Path.home() / ".config" / "genie"
STATE = ROOT / "state"
JOBS = STATE / "jobs"
SCREENSHOTS = Path.home() / "Pictures" / "genie"
DOWNLOADS = Path.home() / "Downloads" / "Genie"
VAULT_FILE = STATE / "vault.json"
VAULT_KEY = STATE / "vault.key"
SCHEDULE_FILE = STATE / "schedules.json"

for d in (STATE, JOBS, SCREENSHOTS):
    d.mkdir(parents=True, exist_ok=True)

# Downloads may point at a removable drive via ~/Downloads. If the drive is
# absent or read-only, keep GENIE importable and store downloads privately
# under its writable configuration directory instead.
try:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
except OSError:
    DOWNLOADS = ROOT / "downloads"
    DOWNLOADS.mkdir(parents=True, exist_ok=True)

_pw = None
_context = None
_page = None

def _ensure_display():
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    for n in range(10):
        sock = Path(f"/tmp/.X11-unix/X{n}")
        if sock.exists():
            os.environ["DISPLAY"] = f":{n}"
            return os.environ["DISPLAY"]
    return None

def _read_openrouter_key():
    env = ROOT / "openrouter.env"
    if not env.exists():
        return ""
    for line in env.read_text(errors="replace").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'").strip('"')
    return ""

def _secret_value(value):
    if isinstance(value, str) and value.startswith("vault://"):
        return _vault_load(value[8:])
    return value
def _vault_init():
    from cryptography.fernet import Fernet
    if not VAULT_KEY.exists():
        VAULT_KEY.write_bytes(Fernet.generate_key())
        os.chmod(VAULT_KEY, 0o600)
    if not VAULT_FILE.exists():
        VAULT_FILE.write_text("{}")
        os.chmod(VAULT_FILE, 0o600)
    return Fernet(VAULT_KEY.read_bytes())

def _vault_load(name):
    f = _vault_init()
    data = json.loads(VAULT_FILE.read_text() or "{}")
    if name not in data:
        raise KeyError(f"Secret not found: {name}")
    return f.decrypt(data[name].encode()).decode()

EXTENDED_WORKSTATION_TOOLS = WORKSTATION_TOOLS

def vault_tool(action, name="", value=None):
    f = _vault_init()
    data = json.loads(VAULT_FILE.read_text() or "{}")
    if action == "set":
        if value is None:
            raise ValueError("value is required for set")
        data[name] = f.encrypt(str(value).encode()).decode()
        VAULT_FILE.write_text(json.dumps(data, indent=2))
        os.chmod(VAULT_FILE, 0o600)
        return {"ok": True, "name": name, "stored": True}
    if action == "get":
        secret = _vault_load(name)
        return {"ok": True, "name": name, "present": True,
                "revealed": False, "secret_chars": len(secret),
                "reference": f"vault://{name}"}
    if action == "exists":
        return {"ok": True, "name": name, "present": name in data}
    if action == "delete":
        existed = name in data
        data.pop(name, None)
        VAULT_FILE.write_text(json.dumps(data, indent=2))
        return {"ok": True, "name": name, "deleted": existed}
    if action == "list":
        return {"ok": True, "names": sorted(data)}
    raise ValueError(f"Unknown vault action: {action}")
def browser_tool(action, **args):
    global _pw, _context, _page
    try:
        if _context is None:
            if not args.get("headless", False) and not _ensure_display():
                return {"ok": False, "error": "No X11 DISPLAY detected."}
            from playwright.sync_api import sync_playwright
            _pw = sync_playwright().start()
            profile = ROOT / "browser-profile"
            _context = _pw.chromium.launch_persistent_context(
                str(profile),
                headless=bool(args.get("headless", False)),
                executable_path=args.get("executable", "/usr/bin/google-chrome"),
                accept_downloads=True,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        if _page is None or _page.is_closed():
            _page = _context.pages[0] if _context.pages else _context.new_page()
        if action == "navigate":
            resp = _page.goto(args["url"],
                              wait_until=args.get("wait_until", "domcontentloaded"),
                              timeout=int(args.get("timeout_ms", 60000)))
            return {"ok": True, "url": _page.url, "title": _page.title(),
                    "status": resp.status if resp else None}
        if action == "current":
            return {"ok": True, "url": _page.url, "title": _page.title()}
        if action == "snapshot":
            limit = min(int(args.get("max_chars", 30000)), 100000)
            data = _page.evaluate("""() => ({
              title: document.title, url: location.href,
              text: document.body ? document.body.innerText : "",
              elements: Array.from(document.querySelectorAll(
                'a,button,input,textarea,select,[role="button"],[role="link"]'
              )).slice(0,500).map((e,i)=>({
                i, tag:e.tagName.toLowerCase(), role:e.getAttribute('role'),
                type:e.getAttribute('type'), name:e.getAttribute('name'),
                aria:e.getAttribute('aria-label'),
                text:(e.innerText||e.value||"").trim().slice(0,240),
                href:e.href||null, disabled:!!e.disabled
              }))
            })""")
            data["text"] = data["text"][:limit]
            return {"ok": True, **data}
        if action == "click":
            locator = _page.locator(args["selector"]).first
            box = locator.bounding_box()
            if box:
                print(f"[action] click x={box['x']:.0f} y={box['y']:.0f} selector={args['selector']}", flush=True)
            else:
                print(f"[action] click selector={args['selector']}", flush=True)
            locator.click(timeout=int(args.get("timeout_ms", 30000)))
            return {"ok": True, "url": _page.url, "title": _page.title()}
        if action == "fill":
            value = _secret_value(args.get("value", ""))
            _page.locator(args["selector"]).first.fill(
                str(value), timeout=int(args.get("timeout_ms", 30000)))
            return {"ok": True, "selector": args["selector"]}
        if action == "press":
            _page.locator(args["selector"]).first.press(
                args["key"], timeout=int(args.get("timeout_ms", 30000)))
            return {"ok": True}
        if action == "select":
            _page.locator(args["selector"]).first.select_option(
                args["value"], timeout=int(args.get("timeout_ms", 30000)))
            return {"ok": True}
        if action == "download":
            with _page.expect_download(timeout=int(args.get("timeout_ms", 60000))) as info:
                _page.locator(args["selector"]).first.click()
            dl = info.value
            dest = Path(args.get("path") or
                        (DOWNLOADS / (dl.suggested_filename or f"download-{int(time.time())}")))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dl.save_as(str(dest))
            return {"ok": True, "path": str(dest), "filename": dl.suggested_filename}
        if action == "screenshot":
            dest = Path(args.get("path") or
                        (SCREENSHOTS / f"browser-{int(time.time())}.png"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            _page.screenshot(path=str(dest), full_page=bool(args.get("full_page", True)))
            return {"ok": True, "path": str(dest), "url": _page.url, "title": _page.title()}
        if action == "start":
            return {"ok": True, "url": _page.url, "title": _page.title()}
        raise ValueError(f"Unknown browser action: {action}")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
def _image_metadata(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return {"format": im.format, "width": im.width, "height": im.height,
                    "mode": im.mode, "bytes": Path(path).stat().st_size}
    except Exception as e:
        return {"bytes": Path(path).stat().st_size, "metadata_error": str(e)}

def desktop_screenshot_tool(path=None):
    if not _ensure_display():
        raise RuntimeError("No X11 DISPLAY detected.")
    dest = Path(path or (SCREENSHOTS / f"desktop-{int(time.time())}.png"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("gnome-screenshot"):
        cmd = ["gnome-screenshot", "-f", str(dest)]
    elif shutil.which("scrot"):
        cmd = ["scrot", str(dest)]
    else:
        raise RuntimeError("No desktop screenshot utility is installed.")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "screenshot failed")
    return {"ok": True, "path": str(dest), **_image_metadata(dest)}

def image_inspect_tool(path, mode="vision", max_chars=4000):
    path = str(Path(os.path.expanduser(path)).resolve())
    if not os.path.isfile(path):
        return {"ok": False, "error": f"Image not found: {path}"}
    meta = _image_metadata(path)
    if mode == "metadata":
        return {"ok": True, "path": path, **meta}
    if mode == "ocr":
        if not shutil.which("tesseract"):
            return {"ok": True, "path": path, **meta, "ocr": "tesseract is not installed"}
        p = subprocess.run(["tesseract", path, "stdout"], capture_output=True,
                           text=True, timeout=60)
        return {"ok": p.returncode == 0, "path": path, **meta,
                "ocr": p.stdout[:max_chars]}
    if not _read_openrouter_key():
        return {"ok": False, "error": "OpenRouter key unavailable", "path": path, **meta}
    try:
        import requests
        from PIL import Image
        from io import BytesIO
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((1600, 1600))
            buf = BytesIO()
            im.save(buf, format="JPEG", quality=78, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        key = _read_openrouter_key()
        models = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            params={"max_price": "0"}, timeout=30).json().get("data", [])
        vision = []
        for m in models:
            mods = set((m.get("architecture") or {}).get("input_modalities")
                       or m.get("input_modalities") or [])
            pricing = m.get("pricing") or {}
            try:
                free = float(pricing.get("prompt", 0) or 0) == 0 and float(
                    pricing.get("completion", 0) or 0) == 0
            except Exception:
                free = False
            if free and "image" in mods:
                vision.append(m["id"])
        if not vision:
            return {"ok": False, "error": "No current free vision model found",
                    "path": path, **meta}
        body = {"model": vision[0], "messages": [{"role":"user","content":[
            {"type":"text","text":"Inspect this image. Describe visible UI, text, controls, errors, layout, and actionable details."},
            {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}
        ]}], "max_tokens": 2000, "temperature": 0.1}
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={"Authorization": f"Bearer {key}",
                                      "Content-Type":"application/json"},
                             json=body, timeout=120)
        resp.raise_for_status()
        desc = resp.json()["choices"][0]["message"].get("content","")
        return {"ok": True, "path": path, "model": vision[0], **meta,
                "description": desc[:max_chars]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "path": path, **meta}
def job_start(command, cwd=None, env=None):
    job = uuid.uuid4().hex[:12]
    cwd = str(Path(os.path.expanduser(cwd or Path.cwd())).resolve())
    log = JOBS / f"{job}.log"
    meta = JOBS / f"{job}.json"
    merged = os.environ.copy()
    for k, v in (env or {}).items():
        merged[str(k)] = str(_secret_value(v))
    out = log.open("w", encoding="utf-8")
    wrapped = f'{command}; code=$?; printf "\\n__GENIE_EXIT__=%s\\n" "$code"'
    p = subprocess.Popen(["/bin/bash", "-lc", wrapped], cwd=cwd, env=merged,
                         stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    out.close()
    meta.write_text(json.dumps({"job_id":job, "pid":p.pid, "command":command,
                                "cwd":cwd, "log":str(log), "started":time.time()}, indent=2))
    return {"ok": True, "job_id": job, "pid": p.pid, "log": str(log), "cwd": cwd}

def _job_meta(job_id):
    p = JOBS / f"{job_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"Unknown job: {job_id}")
    return json.loads(p.read_text())

def job_status(job_id, tail_lines=80):
    m = _job_meta(job_id)
    pid = int(m["pid"])
    try:
        proc_state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        running = proc_state not in {"Z", "X"}
    except Exception:
        running = False
    text = Path(m["log"]).read_text(errors="replace")
    lines = text.splitlines()
    exit_code = None
    for line in reversed(lines):
        hit = re.search(r"__GENIE_EXIT__=(\d+)", line)
        if hit:
            exit_code = int(hit.group(1))
            break
    return {"ok": True, "job_id":job_id, "pid":pid, "running":running,
            "exit_code":exit_code, "command":m["command"], "cwd":m["cwd"],
            "log":m["log"], "output":"\n".join(lines[-int(tail_lines):])}

def job_kill(job_id, sig="TERM"):
    m = _job_meta(job_id)
    signo = getattr(signal, "SIG"+str(sig).upper(), signal.SIGTERM)
    try:
        os.killpg(int(m["pid"]), signo)
        return {"ok": True, "job_id":job_id, "signal":signo, "pid":int(m["pid"])}
    except ProcessLookupError:
        return {"ok": True, "job_id":job_id, "already_stopped":True}
def search_index_tool(path, pattern, max_results=300, glob=None, case_sensitive=False):
    root = str(Path(os.path.expanduser(path)).resolve())
    if not shutil.which("rg"):
        raise RuntimeError("ripgrep (rg) is required for content search")
    cmd = ["rg","--json","--hidden","--glob","!.git","--max-count","30"]
    if not case_sensitive:
        cmd.append("-i")
    if glob:
        cmd += ["--glob", glob]
    cmd += [pattern, root]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    matches = []
    for line in p.stdout.splitlines():
        try:
            obj = json.loads(line)
            if obj.get("type") != "match":
                continue
            d = obj["data"]
            matches.append({"path":d["path"]["text"], "line":d["line_number"],
                            "text":d["lines"]["text"].rstrip("\n")})
            if len(matches) >= int(max_results):
                break
        except Exception:
            pass
    return {"ok": p.returncode in (0,1), "root":root, "pattern":pattern,
            "count":len(matches), "matches":matches,
            "truncated":len(matches) >= int(max_results)}

def patch_editor_tool(action, path=None, old=None, new=None, diff=None,
                      marker=None, cwd=None, replace_all=False):
    if action == "diff":
        if not path or old is None or new is None:
            raise ValueError("diff needs path, old, new")
        before = Path(os.path.expanduser(path)).read_text(errors="replace")
        return {"ok":True, "diff":"".join(__import__("difflib").unified_diff(
            before.splitlines(True), new if isinstance(new,list) else
            before.replace(old,new,1).splitlines(True),
            fromfile=str(path), tofile=str(path)))}
    if action == "apply_unified":
        if not diff:
            raise ValueError("diff is required")
        root = str(Path(os.path.expanduser(cwd or Path.cwd())).resolve())
        p = subprocess.run(["git","apply","--whitespace=nowarn"],
                           cwd=root, input=diff, text=True,
                           capture_output=True, timeout=120)
        return {"ok":p.returncode==0, "exit_code":p.returncode,
                "stdout":p.stdout, "stderr":p.stderr, "cwd":root}
    if not path:
        raise ValueError("path is required")
    target = Path(os.path.expanduser(path)).resolve()
    text = target.read_text(errors="replace")
    backup = target.with_name(target.name + f".genie-backup-{int(time.time())}")
    if action == "replace":
        count = text.count(old)
        if count != 1 and not replace_all:
            raise ValueError(f"Expected one match, found {count}")
        updated = text.replace(old,new, -1 if replace_all else 1)
    elif action == "insert_before":
        if marker not in text: raise ValueError("marker not found")
        updated = text.replace(marker,new+marker,1)
    elif action == "insert_after":
        if marker not in text: raise ValueError("marker not found")
        updated = text.replace(marker,marker+new,1)
    else:
        raise ValueError(f"Unknown patch action: {action}")
    shutil.copy2(target, backup)
    target.write_text(updated)
    return {"ok":True, "path":str(target), "backup":str(backup),
            "bytes_before":len(text), "bytes_after":len(updated)}
def http_client_tool(method, url, headers=None, body=None, timeout=60, auth=None):
    import requests
    h = dict(headers or {})
    if auth and isinstance(auth, dict):
        h.setdefault("Authorization", auth.get("authorization") or
                     auth.get("Authorization") or "")
    for k, v in list(h.items()):
        h[k] = str(_secret_value(v))
    payload = body
    if isinstance(body, str) and body.startswith("vault://"):
        payload = _secret_value(body)
    r = requests.request(method.upper(), url, headers=h, json=payload
                          if isinstance(payload,(dict,list)) else None,
                          data=payload if isinstance(payload,str) else None,
                          timeout=int(timeout))
    try:
        parsed = r.json()
        kind = "json"
    except Exception:
        parsed = r.text
        kind = "text"
    return {"ok":200 <= r.status_code < 400, "status":r.status_code,
            "content_type":r.headers.get("content-type",""), "kind":kind,
            "url":r.url, "data":parsed}

def extract_structured_tool(source=None, text=None, format="auto",
                            path=None, max_rows=1000):
    if path:
        source = Path(os.path.expanduser(path)).read_text(errors="replace")
        if format == "auto":
            suffix = Path(path).suffix.lower()
            format = {".json":"json",".yaml":"yaml",".yml":"yaml",
                      ".csv":"csv"}.get(suffix,"text")
    if source is None:
        source = text or ""
    fmt = format.lower()
    if fmt == "json":
        return {"ok":True,"format":"json","data":json.loads(source)}
    if fmt == "yaml":
        import yaml
        return {"ok":True,"format":"yaml","data":yaml.safe_load(source)}
    if fmt == "csv":
        rows = list(csv.reader(io.StringIO(source)))
        return {"ok":True,"format":"csv","headers":rows[0] if rows else [],
                "rows":rows[1:int(max_rows)+1],
                "row_count":max(0,len(rows)-1)}
    return {"ok":True,"format":"text","data":source}
def _detect_manager(cwd):
    root = Path(os.path.expanduser(cwd or Path.cwd()))
    if (root/"package.json").exists(): return "npm"
    if (root/"Cargo.toml").exists(): return "cargo"
    if (root/"pyproject.toml").exists() or (root/"requirements.txt").exists(): return "python"
    return "python"

def package_manager_tool(action, cwd=None, manager="auto", package=None,
                         version=None, dev=False, command=None):
    cwd = str(Path(os.path.expanduser(cwd or Path.cwd())).resolve())
    manager = _detect_manager(cwd) if manager == "auto" else manager
    if action == "check":
        if command:
            cmd = command
        elif manager == "npm":
            cmd = "npm test && (npm run lint --if-present)"
        elif manager == "cargo":
            cmd = "cargo test"
        else:
            cmd = "pytest -q"
        p = subprocess.run(["/bin/bash","-lc",cmd], cwd=cwd,
                           capture_output=True, text=True, timeout=900)
        combined = (p.stdout+chr(10)+p.stderr).strip()
        failures = [ln for ln in combined.splitlines()
                    if re.search(r"\b(FAIL|FAILED|ERROR|Error|failed)\b", ln)]
        return {"ok":p.returncode==0, "manager":manager, "command":cmd,
                "exit_code":p.returncode, "failures":failures[-100:],
                "output":combined[-20000:]}
    if action not in {"install","update","pin"}:
        raise ValueError("action must be install, update, or pin, or check")
    if manager == "npm":
        if action == "install": cmd = "npm install " + (package or "")
        elif action == "pin": cmd = "npm install " + (package or "") + "@" + str(version)
        else: cmd = "npm update"
        if dev and action in {"install","pin"}: cmd = cmd.replace("npm install","npm install -D",1)
    elif manager == "cargo":
        if action == "install": cmd = "cargo add " + (package or "")
        elif action == "pin": cmd = "cargo add " + (package or "") + "@" + str(version)
        else: cmd = "cargo update"
    elif manager == "python":
        uv = str(Path.home()/".local/bin/uv")
        if Path(uv).exists():
            if action == "install": cmd = uv + " add " + (package or "")
            elif action == "pin": cmd = uv + " add " + shlex.quote((package or "")+"=="+str(version))
            else: cmd = uv + " lock --upgrade"
        else:
            if action == "install": cmd = "python3 -m pip install " + (package or "")
            elif action == "pin": cmd = "python3 -m pip install " + (package or "") + "==" + str(version)
            else: cmd = "python3 -m pip list --outdated"
    elif manager == "apt":
        cmd = "sudo apt-get update && sudo apt-get install -y " + (package or "")
    else:
        raise ValueError(f"Unsupported package manager: {manager}")
    p = subprocess.run(["/bin/bash","-lc",cmd], cwd=cwd,
                       capture_output=True, text=True, timeout=900)
    return {"ok":p.returncode==0, "manager":manager, "command":cmd,
            "exit_code":p.returncode, "stdout":p.stdout[-12000:],
            "stderr":p.stderr[-12000:]}
def schedule_tool(action, name=None, command=None, after_seconds=None,
                  every_seconds=None, message=None):
    if action == "schedule":
        name = name or "genie-"+uuid.uuid4().hex[:8]
        if message and not command:
            command = "notify-send " + shlex.quote("GENIE") + " " + shlex.quote(message)
        if not command:
            raise ValueError("command or message is required")
        delay = int(after_seconds or 0)
        every = int(every_seconds or 0)
        quoted = command
        if every > 0:
            loop = f"sleep {max(delay,0)}; while :; do {quoted}; sleep {every}; done"
        else:
            loop = f"sleep {max(delay,0)}; {quoted}"
        try:
            cfg = Path.home()/".config/systemd/user"
            cfg.mkdir(parents=True, exist_ok=True)
            unit = "genie-"+re.sub(r"[^A-Za-z0-9_.-]","-",name)
            service = cfg/(unit+".service")
            timer = cfg/(unit+".timer")
            exec_line = "/bin/bash -lc " + shlex.quote(command)
            service.write_text("[Unit]\nDescription=GENIE scheduled job\n\n[Service]\nType=oneshot\nExecStart="+exec_line+"\n")
            timer.write_text("[Unit]\nDescription=GENIE timer\n\n[Timer]\nOnActiveSec="+str(max(delay,1))+"s\nUnit="+unit+".service\n")
            subprocess.run(["systemctl","--user","daemon-reload"], check=True,
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["systemctl","--user","enable","--now",unit+".timer"],
                           check=True, capture_output=True, text=True, timeout=10)
            return {"ok":True,"name":name,"backend":"systemd-user","unit":unit}
        except Exception:
            for p in (locals().get("service"), locals().get("timer")):
                if p and Path(p).exists():
                    Path(p).unlink()
            job = job_start(loop)
            schedules = json.loads(SCHEDULE_FILE.read_text()) if SCHEDULE_FILE.exists() else {}
            schedules[name] = {"job_id":job["job_id"],"command":command,
                               "after_seconds":delay,"every_seconds":every}
            SCHEDULE_FILE.write_text(json.dumps(schedules, indent=2))
            return {"ok":True,"name":name,"backend":"background-job",
                    "job_id":job["job_id"],"log":job["log"]}
    if action == "list":
        schedules = json.loads(SCHEDULE_FILE.read_text()) if SCHEDULE_FILE.exists() else {}
        return {"ok":True,"background":schedules}
    if action == "cancel":
        if not name: raise ValueError("name is required")
        schedules = json.loads(SCHEDULE_FILE.read_text()) if SCHEDULE_FILE.exists() else {}
        item = schedules.pop(name, None)
        if item:
            try: job_kill(item["job_id"])
            except Exception: pass
            SCHEDULE_FILE.write_text(json.dumps(schedules, indent=2))
            return {"ok":True,"cancelled":name,"backend":"background-job"}
        return {"ok":False,"error":f"Schedule not found: {name}"}
    raise ValueError("action must be schedule, list, or cancel")

EXT_TOOLS = EXTENDED_WORKSTATION_TOOLS + [
 {"type":"function","function":{"name":"browser","description":"Persistent real browser: navigate, DOM snapshot, click, forms, downloads, screenshots.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","current","navigate","snapshot","click","fill","press","select","download","screenshot"]},"url":{"type":"string"},"selector":{"type":"string"},"value":{"type":"string"},"key":{"type":"string"},"path":{"type":"string"},"full_page":{"type":"boolean"},"headless":{"type":"boolean"},"wait_until":{"type":"string"},"timeout_ms":{"type":"integer"}},"required":["action"]}}},
 {"type":"function","function":{"name":"task","description":"Start, monitor, tail, and kill long-running background jobs.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","status","kill"]},"job_id":{"type":"string"},"command":{"type":"string"},"cwd":{"type":"string"},"env":{"type":"object"},"tail_lines":{"type":"integer"},"signal":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"patch_editor","description":"Surgical file editing: targeted replace, marker insertion, diff generation, and git unified-diff application.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["replace","insert_before","insert_after","diff","apply_unified"]},"path":{"type":"string"},"old":{"type":"string"},"new":{"type":"string"},"marker":{"type":"string"},"diff":{"type":"string"},"cwd":{"type":"string"},"replace_all":{"type":"boolean"}},"required":["action"]}}},
 {"type":"function","function":{"name":"search_index","description":"Search file contents recursively with ripgrep and return structured path/line/text matches.","parameters":{"type":"object","properties":{"path":{"type":"string"},"pattern":{"type":"string"},"max_results":{"type":"integer"},"glob":{"type":"string"},"case_sensitive":{"type":"boolean"}},"required":["path","pattern"]}}},
]
EXT_TOOLS += [
 {"type":"function","function":{"name":"package_manager","description":"Install, pin, update dependencies, or run tests/linters and parse failures.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["install","pin","update","check"]},"cwd":{"type":"string"},"manager":{"type":"string","enum":["auto","python","npm","cargo","apt"]},"package":{"type":"string"},"version":{"type":"string"},"dev":{"type":"boolean"},"command":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"http_client","description":"Direct HTTP REST/JSON client with headers, auth, JSON parsing, and structured responses.","parameters":{"type":"object","properties":{"method":{"type":"string"},"url":{"type":"string"},"headers":{"type":"object"},"body":{},"timeout":{"type":"integer"},"auth":{"type":"object"}},"required":["method","url"]}}},
 {"type":"function","function":{"name":"extract_structured","description":"Parse JSON, YAML, or CSV from text or a file into structured data.","parameters":{"type":"object","properties":{"source":{"type":"string"},"text":{"type":"string"},"format":{"type":"string","enum":["auto","json","yaml","csv","text"]},"path":{"type":"string"},"max_rows":{"type":"integer"}}}}},
 {"type":"function","function":{"name":"screenshot","description":"Capture the visible X11 desktop to a PNG for verification.","parameters":{"type":"object","properties":{"path":{"type":"string"}}}}},
 {"type":"function","function":{"name":"image_inspect","description":"Inspect an image by metadata, OCR, or a free OpenRouter vision model.","parameters":{"type":"object","properties":{"path":{"type":"string"},"mode":{"type":"string","enum":["vision","ocr","metadata"]},"max_chars":{"type":"integer"}},"required":["path"]}}},
 {"type":"function","function":{"name":"schedule","description":"Schedule one-shot or recurring local commands/desktop reminders with systemd user timers.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["schedule","list","cancel"]},"name":{"type":"string"},"command":{"type":"string"},"after_seconds":{"type":"integer"},"every_seconds":{"type":"integer"},"message":{"type":"string"}},"required":["action"]}}},
 {"type":"function","function":{"name":"vault","description":"Encrypted local credential vault. Stored secrets are never returned; use vault://name references with tools that accept them.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["set","get","exists","delete","list"]},"name":{"type":"string"},"value":{"type":"string"}},"required":["action"]}}}
]

def execute_extended_tool(name, args):
    workstation = execute_workstation_tool(name, args)
    if workstation is not None:
        return workstation
    if name == "browser":
        return browser_tool(args.get("action"), **{k:v for k,v in args.items() if k!="action"})
    if name == "task":
        action = args.get("action")
        if action == "start": return job_start(args["command"], args.get("cwd"), args.get("env"))
        if action == "status": return job_status(args["job_id"], args.get("tail_lines",80))
        if action == "kill": return job_kill(args["job_id"], args.get("signal","TERM"))
    if name == "patch_editor":
        return patch_editor_tool(**args)
    if name == "search_index":
        return search_index_tool(**args)
    if name == "package_manager":
        return package_manager_tool(**args)
    if name == "http_client":
        return http_client_tool(**args)
    if name == "extract_structured":
        return extract_structured_tool(**args)
    if name == "screenshot":
        return desktop_screenshot_tool(args.get("path"))
    if name == "image_inspect":
        return image_inspect_tool(**args)
    if name == "schedule":
        return schedule_tool(**args)
    if name == "vault":
        return vault_tool(args.get("action"), args.get("name",""), args.get("value"))
    return None
