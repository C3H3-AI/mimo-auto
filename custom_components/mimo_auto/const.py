"""Constants for the MiMo Auto integration.

This integration bridges HA with the MiMo Code Addon.
The Addon runs `mimo serve`, IM channels (Feishu/WeChat), and WebUI.
This integration provides:
- Conversation agent (HA voice assistant / UI chat)
- Chat service (automations)
- Addon status sensor
- Addon lifecycle management via Supervisor API
"""

DOMAIN = "mimo_auto"
DOMAIN_NAME = "MiMo Auto"

# Configuration
CONF_SERVER_URL = "server_url"
CONF_WEBUI_URL = "webui_url"
CONF_USE_SUPERVISOR = "use_supervisor"

# Defaults
DEFAULT_SERVER_URL = "http://127.0.0.1:14096"
DEFAULT_WEBUI_URL = "http://127.0.0.1:8099"
DEFAULT_USE_SUPERVISOR = True

# Timeouts
HEALTH_CHECK_INTERVAL_SECONDS = 30
MESSAGE_TIMEOUT_SECONDS = 180  # 3 minutes
ADDON_DETECT_TIMEOUT_SECONDS = 10
API_TIMEOUT_SECONDS = 10

# Addon slugs
ADDON_SLUG = "mimo-code"
ADDON_SLUG_LOCAL = "local_mimo-code"

# Conversation attributes
ATTR_MESSAGE = "message"
ATTR_SESSION_ID = "session_id"
ATTR_MODEL = "model"
ATTR_RESPONSE = "response"
ATTR_ERROR = "error"

# API endpoints (relative to server_url)
API_CREATE_SESSION = "/session"
API_SEND_MESSAGE = "/session/{session_id}/message"
API_GET_MESSAGES = "/session/{session_id}/message"

# Error messages
ERROR_SERVER_NOT_RUNNING = "MiMo Code Addon 未运行，请检查 Addon 状态。"
ERROR_CONNECTION_FAILED = "无法连接到 MiMo Code Addon，请检查配置。"
ERROR_TIMEOUT = "MiMo 请求超时，请稍后重试。"
ERROR_ADDON_NOT_FOUND = "未检测到 MiMo Code Addon，请确认已安装并启动。"

# Service names
SERVICE_CHAT = "chat"
