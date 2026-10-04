#!/usr/bin/env python3
"""Puter REST fallback (PAID plan only).

The OpenAI-compatible endpoints (/puterai/openai/v1/*) require a paid Puter
plan; free accounts receive `402 subscription_required`. The free path for
GENIE is the Puter.js bridge in puter_bridge.py. This module is only used
when PUTER_AUTH_TOKEN is configured AND the free bridge is unavailable.
"""
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
