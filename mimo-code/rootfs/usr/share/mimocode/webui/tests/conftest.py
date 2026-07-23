"""Pytest configuration: make the webui package importable.

The three modules under test (client.py, channel_manager.py, feishu_client.py)
live one directory up from this tests/ folder. Insert that directory onto
sys.path so the tests can ``import client``, ``import channel_manager`` and
``import feishu_client`` directly.

Per-test import stubbing (sys.modules injection) is done inside each test
module, NOT here, so the stub surface stays isolated per module.
"""

from pathlib import Path
import sys

WEBUI_DIR = str(Path(__file__).resolve().parent.parent)
if WEBUI_DIR not in sys.path:
    sys.path.insert(0, WEBUI_DIR)
