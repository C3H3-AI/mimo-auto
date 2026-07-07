#!/usr/bin/env python3
"""Test MiMo API with different keys"""
import json
import urllib.request

api_url = "https://token-plan-sgp.xiaomimimo.com/v1/chat/completions"
api_key = "tp-sxthz0z7108xos912hecuflqh69ft2o1hn4hozx35p9usaxe"

data = json.dumps({
    "model": "mimo-v2.5",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 10
}).encode()

req = urllib.request.Request(
    api_url,
    data=data,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
)

try:
    resp = urllib.request.urlopen(req, timeout=10)
    print("Status:", resp.status)
    print("Response:", resp.read().decode()[:500])
except Exception as e:
    print("Error:", e)
