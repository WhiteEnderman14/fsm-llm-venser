# Image of the engine. Ollama runs in its own container (see compose.yaml).
# Full image names, so that Docker and Podman both accept the file.
FROM docker.io/library/python:3.12-slim

# graphviz: the `dot` program used by the `draw` command
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Which LLM packages to install: ollama, gemini, claude (space separated). Change it with
#   EXTRAS="ollama gemini claude" docker compose build      (or a line EXTRAS=... in the .env file)
ARG EXTRAS=ollama

# dependencies first, so that the layer is cached when only the code changes
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

COPY examples ./examples

# `docker run IMAGE validate examples/.../machine.yaml` runs the command line directly
ENTRYPOINT ["fsm-llm-venser"]
CMD ["--help"]
