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
MESSAGE_TIMEOUT_SECONDS = 60
SERVER_START_TIMEOUT_SECONDS = 30
SERVER_STOP_TIMEOUT_SECONDS = 10

# Process management
MAX_RESTART_ATTEMPTS = 3
PROCESS_NAME = "mimo"

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
