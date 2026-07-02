# ============================================================
# Stage 1: Build — Install @mimo-ai/cli and extract native binary
# ============================================================
ARG BUILD_FROM
ARG NODE_VERSION="22"
FROM node:${NODE_VERSION}-alpine AS builder

# Install mimo CLI (native binary is bundled in the npm package)
RUN npm install -g @mimo-ai/cli@0.1.4

# ============================================================
# Stage 2: Runtime — HA base image, only the binary, no Node.js
# ============================================================
FROM ${BUILD_FROM}

# Copy the mimocode native binary (ELF, no Node.js needed at runtime)
# The platform-specific path is: @mimo-ai/mimocode-{os}-{arch}[-musl]/bin/mimo
# The builder has only ONE platform variant installed, so we copy the whole
# @mimo-ai directory and let the entry script resolve the correct binary.
COPY --from=builder \
    /usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/ \
    /usr/local/lib/mimo/

# Also copy the CLI entry script (used as the canonical "mimo" command)
COPY --from=builder \
    /usr/local/lib/node_modules/@mimo-ai/cli/bin/ \
    /usr/local/lib/mimo-cli/bin/
COPY --from=builder \
    /usr/local/lib/node_modules/@mimo-ai/cli/package.json \
    /usr/local/lib/mimo-cli/

# Create symlink so `mimo` works from PATH
RUN ln -sf /usr/local/lib/mimo-cli/bin/mimo /usr/local/bin/mimo

# Add bashio for config access
RUN apk add --no-cache bashio

# Copy s6-overlay service files
COPY rootfs/ /

# Arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="MiMo Auto Team <https://github.com/C3H3-AI/mimo-auto>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="MiMo Auto Team" \
    org.opencontainers.image.authors="https://github.com/C3H3-AI/mimo-auto" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/C3H3-AI/mimo-auto" \
    org.opencontainers.image.source="https://github.com/C3H3-AI/mimo-auto" \
    org.opencontainers.image.documentation="https://github.com/C3H3-AI/mimo-auto" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.version=${BUILD_VERSION} \
    org.opencontainers.image.revision=${BUILD_REF}

# Entrypoint is s6-overlay (provided by the HA base image via /init)
ENTRYPOINT ["/init"]