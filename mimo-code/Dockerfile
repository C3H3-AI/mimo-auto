# ============================================================
# Stage 1: Build — Install @mimo-ai/cli and extract native binary
# The builder is node:22-alpine which runs on the build host CPU arch.
# Docker Buildx + QEMU emulates the target arch, so npm will install
# the correct platform variant of @mimo-ai/mimocode-{arch}[-musl].
# ============================================================
ARG BUILD_FROM
ARG NODE_VERSION="22"
FROM node:${NODE_VERSION}-alpine AS builder

RUN npm install -g @mimo-ai/cli@0.1.4

# ============================================================
# Stage 2: Runtime — HA base image, only the native ELF binary
# The @mimo-ai/cli bin/mimo is a Node.js wrapper; we skip it and
# link directly to the platform-specific native binary instead.
# No Node.js needed at runtime.
# ============================================================
FROM ${BUILD_FROM}

# Copy platform-specific native binary directories only
# (e.g. mimocode-linux-x64, mimocode-linux-arm64-musl)
COPY --from=builder \
    /usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/ \
    /usr/local/lib/mimo/

# Find the native ELF binary and symlink it to /usr/local/bin/mimo
# The native binary lives at @mimo-ai/mimocode-{platform}/bin/mimo.
# There is exactly one such binary (the builder only installs its own arch).
RUN for dir in /usr/local/lib/mimo/mimocode-linux-*/bin; do \
        if [ -f "$dir/mimo" ]; then \
            ln -sf "$dir/mimo" /usr/local/bin/mimo && \
            echo "Linked native mimo binary: $dir/mimo" && \
            exit 0; \
        fi; \
    done && \
    echo "ERROR: No native mimo binary found in /usr/local/lib/mimo" && exit 1

# bashio is already included in the HA base image
# Install Python3 for the Web UI server
RUN sed -i 's|https://dl-cdn.alpinelinux.org|http://mirrors.aliyun.com|g' /etc/apk/repositories && \
    apk add --no-cache python3

# Copy s6-overlay service files and Web UI
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
    maintainer="C3H3-AI <https://github.com/C3H3-AI/mimo-auto>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="C3H3-AI" \
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