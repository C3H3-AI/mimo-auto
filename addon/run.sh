#!/usr/bin/with-contenv bashio
# ============================================================
# MiMo Code add-on for Home Assistant
# Runs mimo serve on the configured port
# ============================================================

set -e

PORT=$(bashio::config 'port')
PRINT_LOGS=$(bashio::config 'print_logs')

bashio::log.info "Starting MiMo Code server on port ${PORT}..."

# Resolve the correct mimo binary
# On Alpine/musl, the wrapper script may pick the wrong binary
MIMO_BIN="/usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/mimocode-linux-arm64-musl/bin/mimo"

if [ -f "$MIMO_BIN" ]; then
    bashio::log.info "Using musl binary: ${MIMO_BIN}"
elif command -v mimo &>/dev/null; then
    MIMO_BIN="mimo"
    bashio::log.info "Using system mimo: $(which mimo)"
else
    bashio::log.error "mimo binary not found!"
    exit 1
fi

# Build args
ARGS="serve --port ${PORT}"
if [ "$PRINT_LOGS" = "true" ]; then
    ARGS="${ARGS} --print-logs"
fi

bashio::log.info "Executing: ${MIMO_BIN} ${ARGS}"
exec "${MIMO_BIN}" ${ARGS}
