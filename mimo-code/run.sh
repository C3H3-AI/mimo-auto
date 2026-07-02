#!/usr/bin/env bashio
# ============================================================
# MiMo Code Add-on — Entry Point
# ============================================================
set -e

CONFIG_PATH=/data/options.json

# Read configuration via bashio
PORT=$(bashio::config 'port')
LOG_LEVEL=$(bashio::config 'log_level')

# Set log level
bashio::log.level "${LOG_LEVEL}"

bashio::log.info "Starting MiMo Code AI Server..."
bashio::log.info "Port: ${PORT}"

# Ensure the binary exists
MIMO_BIN="/usr/local/bin/mimo"
if [ ! -f "${MIMO_BIN}" ]; then
    bashio::log.error "MiMo binary not found at ${MIMO_BIN}"
    exit 1
fi

# Start the server
# mimo serve listens on 0.0.0.0:{PORT} and handles AI agent requests
exec "${MIMO_BIN}" serve --port "${PORT}"