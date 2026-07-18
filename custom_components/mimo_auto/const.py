"""Constants for the MiMo Auto integration."""

DOMAIN = "mimo_auto"
DOMAIN_NAME = "MiMo Auto"

# Configuration
CONF_PORT = "port"
CONF_MIMO_BIN = "mimo_bin_path"
CONF_AUTO_INSTALL = "auto_install"

# Defaults
DEFAULT_PORT = 14096
DEFAULT_MIMO_BIN = "mimo"
DEFAULT_AUTO_INSTALL = True

# Timeouts
HEALTH_CHECK_INTERVAL_SECONDS = 30
MESSAGE_TIMEOUT_SECONDS = 180  # 3 minutes - mimo serve with reasoning can be slow
SERVER_START_TIMEOUT_SECONDS = 30
SERVER_STOP_TIMEOUT_SECONDS = 10

# Process management
MAX_RESTART_ATTEMPTS = 3
PROCESS_NAME = "mimo"
ADDON_SLUG = "mimo-code"
# Alternative slug for locally installed addon
ADDON_SLUG_LOCAL = "local_mimo-code"

# Conversation attributes
ATTR_MESSAGE = "message"
ATTR_SESSION_ID = "session_id"
ATTR_MODEL = "model"
ATTR_RESPONSE = "response"
ATTR_ERROR = "error"

# API endpoints
API_CREATE_SESSION = "/session"
API_SEND_MESSAGE = "/session/{session_id}/message"
API_GET_MESSAGES = "/session/{session_id}/message"

# SSE event types
SSE_EVENT_MESSAGE_V2 = "MessageV2"
SSE_EVENT_DONE = "done"
SSE_EVENT_ERROR = "error"

# Error messages
ERROR_SERVER_NOT_RUNNING = "MiMo Auto server is not running. Please check the configuration."
ERROR_SERVER_START_FAILED = "Failed to start MiMo Auto server."
ERROR_SERVER_CRASHED = "MiMo Auto server has crashed."
ERROR_CONNECTION_FAILED = "Could not connect to MiMo Auto server."
ERROR_TIMEOUT = "MiMo Auto request timed out."
ERROR_INVALID_RESPONSE = "Received an invalid response from MiMo Auto server."

# Service names
SERVICE_CHAT = "chat"

# ==================== Intent Routing ====================

# Intent types for routing to appropriate backend
INTENT_DEVICE_CONTROL = "device_control"
INTENT_SYSTEM_OPERATION = "system_operation"
INTENT_QUERY = "query"
INTENT_GENERAL = "general"

# Device control patterns (Chinese + English)
DEVICE_CONTROL_PATTERNS = {
    "light": ["灯", "灯光", "开灯", "关灯", "light", "lamp"],
    "switch": ["开关", "打开", "关闭", "switch", "turn on", "turn off"],
    "climate": ["空调", "温度", "climate", "temperature", "heat", "cool"],
    "cover": ["窗帘", "卷帘", "百叶窗", "cover", "blind", "curtain"],
    "lock": ["门锁", "锁", "lock", "unlock"],
    "fan": ["风扇", "换气", "fan"],
    "vacuum": ["扫地机", "吸尘器", "vacuum", "clean"],
    "media_player": ["电视", "音箱", "播放器", "media", "player", "tv"],
    "automation": ["自动化", "自动化", "automation", "trigger"],
    "scene": ["场景", "scene"],
    "script": ["脚本", "script"],
}

# System operation patterns
SYSTEM_OPERATION_PATTERNS = {
    "restart": ["重启", "restart", "reboot"],
    "update": ["更新", "升级", "update", "upgrade"],
    "backup": ["备份", "backup"],
    "restore": ["恢复", "restore"],
    "logs": ["日志", "log"],
    "status": ["状态", "status", "health"],
}

# ==================== MCP Configuration ====================

# MCP tool categories
MCP_CATEGORIES = [
    "light", "switch", "climate", "cover", "lock", "fan",
    "vacuum", "media_player", "automation", "scene", "script",
    "sensor", "binary_sensor", "input_boolean", "input_number",
    "input_select", "input_text", "input_datetime",
]

# ==================== SSH Configuration ====================

# SSH connection defaults
CONF_SSH_HOST = "ssh_host"
CONF_SSH_PORT = "ssh_port"
CONF_SSH_USERNAME = "ssh_username"
CONF_SSH_KEY_PATH = "ssh_key_path"

DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USERNAME = "root"

# ==================== Supervisor Configuration ====================

# Supervisor API configuration
CONF_SUPERVISOR_TOKEN = "supervisor_token"
SUPERVISOR_API_TIMEOUT = 10
