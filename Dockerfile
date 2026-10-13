# Synty Asset MCP — server + Blender headless for conversions.
FROM python:3.12-slim-bookworm

ARG BLENDER_VERSION=4.2.3
ARG BLENDER_SERIES=4.2
ARG WITH_PLAYWRIGHT=0

# Runtime libraries Blender needs even in --background mode (no X server).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl xz-utils git \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libsm6 libice6 \
      libgl1 libegl1 libglu1-mesa libgomp1 libfreetype6 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
      | tar -xJ -C /opt \
    && ln -s /opt/blender-${BLENDER_VERSION}-linux-x64/blender /usr/local/bin/blender \
    && blender --version | head -1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && if [ "$WITH_PLAYWRIGHT" = "1" ]; then \
         pip install --no-cache-dir "playwright>=1.45" && playwright install --with-deps chromium; \
       fi

# Mount points must exist (owned by the app user) so fresh named volumes inherit the ownership.
RUN useradd --create-home --uid 1000 synty \
    && mkdir -p /data/archives /data/library /data/converted /data/catalogs /data/state /data/secrets /data/inbox \
    && chown -R synty:synty /data && chmod 700 /data/secrets
USER synty

ENV SYNTY_MCP_DATA_DIR=/data \
    SYNTY_MCP_BLENDER=/usr/local/bin/blender \
    SYNTY_MCP_HOST=0.0.0.0 \
    SYNTY_MCP_PORT=8765 \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz').status==200 else 1)"

ENTRYPOINT ["synty-mcp"]
CMD ["serve", "--transport", "http"]
