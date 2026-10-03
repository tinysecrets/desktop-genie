#!/usr/bin/env python3
"""GENIE execution policy: capability is preserved, authority is bounded."""
from pathlib import Path
import os, re, shlex, time, uuid, json
ROOT=Path.home()/".config"/"genie"
STATE=ROOT/"state"
POLICY_FILE=STATE/"policy.json"
DEFAULT={"mode":"guarded","auto_read":True,"auto_local_safe":True,"require_approval_for":["system","destructive","credentials","network_change","package_change","service_change","desktop_input_risky","external_write"],"allowed_roots":[str(Path.home()/".config"/"genie"),str(Path.home()/ "dai-assistant"),str(Path.home()/ "workspace/projects/active"),str(Path.home()/ "Desktop"),str(Path.home()/ "Documents"),str(Path.home()/ "Downloads"),str(Path.home()/ "Pictures"),str(Path.home()/ "Music"),str(Path.home()/ "Videos"),"/tmp"]}

def load():
    STATE.mkdir(parents=True,exist_ok=True)
    if POLICY_FILE.exists():
        try:
            d=json.loads(POLICY_FILE.read_text())
            x=dict(DEFAULT); x.update(d); return x
        except Exception: pass
    POLICY_FILE.write_text(json.dumps(DEFAULT,indent=2))
    return dict(DEFAULT)

def within(path, roots=None):
    try:
        p=Path(os.path.expanduser(str(path))).resolve()
        return any(p==Path(r).resolve() or Path(r).resolve() in p.parents for r in (roots or load()["allowed_roots"]))
    except Exception: return False

DANGEROUS=re.compile(r"(^|[;&|]\s*)(sudo|su|doas|pkexec|rm|mv|cp|chmod|chown|chgrp|setfacl|mount|umount|mkfs|fdisk|parted|dd|wipefs|shutdown|reboot|poweroff|systemctl|service|iptables|nft|ufw|apt|apt-get|dnf|pacman|pip|npm|pnpm|yarn|cargo)\b",re.I)
DESTRUCTIVE=re.compile(r"\b(rm\s+-rf|git\s+(reset\s+--hard|clean\s+-fdx|checkout\s+--)|drop\s+database|truncate\s+table|delete\s+from)\b",re.I)
CREDENTIALS=re.compile(r"(\.ssh|\.gnupg|\.aws|\.config/.*(token|secret|credential)|vault\.json|vault\.key|openrouter\.env|\.env(?:\.|$)|private.?key)",re.I)

def classify_command(command):
    s=str(command or "").strip()
    if DESTRUCTIVE.search(s): return "destructive"
    if CREDENTIALS.search(s): return "credentials"
    if DANGEROUS.search(s): return "system"
    if re.search(r"\b(curl|wget|git\s+(push|clone|fetch)|scp|rsync)\b",s,re.I): return "external_write"
    if re.search(r"\b(systemctl|service)\b",s,re.I): return "service_change"
    if re.search(r"\b(apt|apt-get|pip|npm|pnpm|yarn|cargo)\s+(install|remove|uninstall|update|upgrade|add)\b",s,re.I): return "package_change"
    return "read_or_local_safe"

def classify_tool(name,args):
    a=args or {}; n=str(name)
    if n in {"read_file","list_directory","find_files","search_index","system_info","capabilities","security_scan","browser_inspect","netdiag","memory","symbol_index"}: return "read_or_local_safe"
    if n in {"clipboard_get","screenshot","image_inspect","web_search"}: return "read_or_local_safe"
    if n in {"desktop_key","desktop_type"}: return "desktop_input_risky"
    if n in {"service","watchdog"} and str(a.get("action","")) in {"start","stop","restart","reload","enable","disable"}: return "service_change"
    if n=="android" and str(a.get("action","")) in {"shell","install","uninstall","push","pull","input","reboot"}: return "system"
    if n in {"package_manager"} and str(a.get("action",""))!="check": return "package_change"
    if n in {"patch_editor","snapshot","build_repair","schedule","task","git","shell","http_client"}: return "external_write" if n=="http_client" else "system"
    if n in {"window"} and str(a.get("action","")) in {"close","kill","launch","sendkey","type"}: return "desktop_input_risky"
    if n in {"vault"} and str(a.get("action","")) in {"set","delete"}: return "credentials"
    return "local_change"

def check_path(path):
    if not path: return True
    return within(path)

def needs_approval(name,args):
    cat=classify_tool(name,args)
    if cat not in load()["require_approval_for"]: return False,cat,""
    return True,cat,f"GENIE approval required for {cat}: {name}"

def authorize(name,args,approved=False):
    a,cat,reason=needs_approval(name,args)
    if a and not approved:
        return False,{"ok":False,"blocked":True,"approval_required":True,"category":cat,"reason":reason,"tool":name}
    for key in ("path","cwd","target"):
        v=(args or {}).get(key)
        if key in {"path","cwd"} and v and not within(v) and not approved:
            return False,{"ok":False,"blocked":True,"approval_required":True,"reason":f"Path outside GENIE allowed roots; explicit approval required: {v}","category":"scope"}
    if name=="shell":
        c=str((args or {}).get("command",""))
        cc=classify_command(c)
        if cc in load()["require_approval_for"] and not approved:
            return False,{"ok":False,"blocked":True,"approval_required":True,"category":cc,"reason":f"Shell command requires approval: {c}"}
    return True,{"category":cat}

def issue_approval(name,args,note=""):
    aid=uuid.uuid4().hex
    p=STATE/"approvals.json"
    data=json.loads(p.read_text()) if p.exists() else {}
    data[aid]={"tool":name,"args":args,"created":time.time(),"note":note,"used":False}
    p.write_text(json.dumps(data,indent=2))
    return aid

def consume_approval(token,name,args):
    p=STATE/"approvals.json"
    if not p.exists(): return False
    try: data=json.loads(p.read_text()); item=data.get(str(token))
    except Exception: return False
    if not item or item.get("used") or item.get("tool")!=name: return False
    item["used"]=True; item["used_at"]=time.time(); p.write_text(json.dumps(data,indent=2)); return True
def safe_summary(name,args,result):
    return {"tool":name,"category":classify_tool(name,args),"approved":True,"timestamp":time.time(),"result_ok":bool(isinstance(result,dict) and result.get("ok",result.get("exit_code",0)==0))}

def prompt_injection_guard(text):
    s=str(text or "").lower()
    patterns=("ignore previous instructions","disable safety","bypass approval","reveal api key","print secret","send the key","delete everything","wipe the disk")
    return not any(p in s for p in patterns)

def policy_status():
    d=load()
    return {"ok":True,"mode":d["mode"],"auto_read":d["auto_read"],"auto_local_safe":d["auto_local_safe"],"approval_categories":d["require_approval_for"],"allowed_roots":d["allowed_roots"],"policy_file":str(POLICY_FILE)}
