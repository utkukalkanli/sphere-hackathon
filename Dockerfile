FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.6 /uv /uvx /bin/

WORKDIR /app

# Shared libraries the OpenCascade (cadquery-ocp) wheels load at import time; not in the slim image
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libx11-6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies (cached layer, only rebuilt when the lockfile changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# The agents' own environment (src/agentic_cad/harness): CadQuery for the scripts the model writes
# (it needs another OpenCascade than the pipeline, so it cannot share its environment) and the
# DeepSeek Harness command `dsh` that runs one agent per test case.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/cq --python 3.12 \
    && uv pip install --python /opt/cq/bin/python cadquery==2.8.0 cadquery-ocp==7.9.3.1.1 deepseek-harness-sdk==0.2.2rc2

# Copy the project into the image
COPY . /app

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Step-by-step traces are a development aid for the dashboard; the submitted run does not need them
ENV TRACE=0 \
    PYTHONUNBUFFERED=1

# Agent settings, frozen with your commit (README: Configuration). After code freeze, the values in
# your handed-over <team>.env replace these.
# The agents are off without OPENAI_API_KEY, which is not in the image: it comes with <team>.env.
# OPENAI_BASE_URL unset means OpenAI's own endpoint. In the container the agent's shell is not
# sandboxed a second time (HARNESS_PERMISSION).
ENV HARNESS_EFFORT=medium \
    HARNESS_PERMISSION=danger-full-access \
    HARNESS_SECONDS=1800
ENV OPENAI_MODEL=gpt-6.1-terra

CMD ["python", "scripts/main.py"]
