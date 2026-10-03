#!/usr/bin/env python3
import json, os, urllib.request, urllib.error

MODEL="nousresearch/hermes-4-405b"
CHAT_URL="https://api.puter.com/puterai/openai/v1/chat/completions"

def chat(messages, tools=None, max_tokens=12000, temperature=0.2):
    body={"model":MODEL,"messages":messages,"max_tokens":max_tokens,"temperature":temperature}
    if tools:
        body["tools"]=tools
        body["tool_choice"]="auto"
    token=os.environ.get("PUTER_AUTH_TOKEN","").strip()
    headers={"Content-Type":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    req=urllib.request.Request(CHAT_URL,data=json.dumps(body).encode(),headers=headers,method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:
        data=json.loads(r.read().decode())
    choices=data.get("choices") or []
    if not choices:
        raise RuntimeError("PUTER_EMPTY_RESPONSE")
    return choices[0].get("message") or {}
