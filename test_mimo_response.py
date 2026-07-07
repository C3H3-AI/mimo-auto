#!/usr/bin/env python3
"""Test mimo serve response time"""
import urllib.request
import json
import time

session_id = "ses_0c9e8c262ffeA2p8h0aoTe2MX8"
url = f"http://127.0.0.1:14095/session/{session_id}/message"

data = json.dumps({
    "message": "hello",
    "parts": [{"type": "text", "text": "hello"}]
}).encode()

req = urllib.request.Request(
    url,
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)

print(f"Testing mimo serve response time...")
start_time = time.time()

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = resp.read().decode()
        elapsed = time.time() - start_time
        print(f"Response time: {elapsed:.2f} seconds")
        print(f"Response length: {len(result)} bytes")
        print(f"Response preview: {result[:200]}")
except Exception as e:
    elapsed = time.time() - start_time
    print(f"Error after {elapsed:.2f} seconds: {e}")
