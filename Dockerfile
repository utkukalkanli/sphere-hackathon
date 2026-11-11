FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /uvx /bin/

# Shared libraries the OpenCascade (cadquery-ocp) and VTK wheels link against
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libx11-6 libxi6 libsm6 libice6 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies (cached layer, only rebuilt when the lockfile changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Copy the project into the image
COPY . /app

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# The container runs as a non-root user that may only write to the output directory and /tmp
ENV HOME=/tmp \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    PYTHONUNBUFFERED=1

# Fail the build, not the run, if the CAD kernel cannot be imported or cannot write a STEP file
RUN python -c "import cadquery as cq; cq.exporters.export(cq.Workplane().box(1, 2, 3), '/tmp/smoke.stp'); print('cadquery', cq.__version__)" \
    && rm -f /tmp/smoke.stp

# Agent settings, frozen with your commit (README: Configuration). After code freeze, the values in
# your handed-over <team>.env replace these.
# ENV OPENAI_BASE_URL=http://<your Brev instance>:8000/v1 \
#     OPENAI_API_KEY=<your vLLM --api-key> \
#     OPENAI_MODEL=<model>
# Loop settings (defaults shown; see src/agentic_cad/config.py). The model must accept images
# unless AGENT_USE_IMAGES=0.
ENV AGENT_MAX_ITERS=6 \
    AGENT_WORKERS=3 \
    AGENT_TIME_BUDGET_S=1020 \
    AGENT_USE_IMAGES=1 \
    AGENT_FEEDBACK_IMAGES=1

CMD ["python", "scripts/main.py"]
