#!/bin/bash
python3 -c "
import json
with open('/config/.storage/core.config_entries') as f:
    d = json.load(f)
    entries = d.get('data', {}).get('entries', [])
    for e in entries:
        if e.get('domain') == 'http':
            token = e.get('data', {}).get('access_token', '')
            if token:
                print(token)
                break
"
