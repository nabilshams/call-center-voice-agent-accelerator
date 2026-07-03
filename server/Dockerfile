###############################################
# Base stage (shared between build and final) #
###############################################
FROM python:3.12-slim-bookworm AS base

# Patch system pip so Defender sees the fixed version
RUN pip install --no-cache-dir --upgrade pip==25.3

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

###############
# Build stage #
###############
FROM base AS builder

## Install and configure UV, see https://docs.astral.sh/uv/ for more information
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# - Silence uv complaining about not being able to use hard links,
# - tell uv to byte-compile packages for faster application startups,
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Create virtual environment explicitly
RUN python -m venv /app/.venv

# Upgrade pip in the virtual environment to fix security vulnerability GHSA-4xh5-x5gv-qwph
RUN /app/.venv/bin/pip install --upgrade pip

# Copy dependency files
COPY pyproject.toml uv.lock /app/

# Sync the project into a new environment, using the frozen lockfile
RUN uv sync --frozen --no-dev --no-install-project --no-editable --all-packages

# Ensure Native MAF SDK is available at runtime for maf orchestration.
# Pin the meta package to one pre-release so core + azure-ai + azure-ai-projects
# are co-versioned. An unpinned/skewed install (e.g. newer core, older azure-ai)
# breaks `from agent_framework.azure import AzureAIAgentClient` with
# `ImportError: cannot import name 'use_observability'`, which silently drops the
# orchestrator to the deterministic fallback. Note: Microsoft Agent Framework
# only ships as pre-releases (1.0.0rc*/b*); the "stable" 1.x on PyPI is a
# different package whose `agent_framework.azure` lacks `AzureAIAgentClient`.
# The orchestrator consumes Foundry *prompt* agents via
# `agent_framework.foundry.FoundryAgent`, so pin `agent-framework-foundry` to the
# same pre-release explicitly (it is pulled transitively via core[all], but the
# extra is unpinned upstream).
RUN /app/.venv/bin/pip install --no-cache-dir --pre \
    "agent-framework==1.0.0rc6" \
    "agent-framework-foundry==1.0.0rc6"

COPY *.py *.md /app/
COPY app /app/app/
COPY static /app/static/
COPY prompts /app/prompts/

###############
# Final image #
###############
FROM base

# Install ffmpeg for audio conversion (required by pydub)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app
RUN useradd -r -d /app -g app -N app

# Copy built app from builder stage and set ownership
COPY --from=builder --chown=app:app /app /app

WORKDIR /app

# Switch to non-root user
USER app

EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/python",  "server.py"]
