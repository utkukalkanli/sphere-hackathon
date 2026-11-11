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

# Copy the project into the image
COPY . /app

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Step-by-step traces are a development aid for the dashboard; the submitted run does not need them
ENV TRACE=0

# Agent settings, frozen with your commit (README: Configuration). After code freeze, the values in
# your handed-over <team>.env replace these.
# ENV OPENAI_BASE_URL=http://<your Brev instance>:8000/v1 \
#     OPENAI_API_KEY=<your vLLM --api-key> \
#     OPENAI_MODEL=<model>
# Step 3 (model-planned cuts) runs only when OPENAI_MODEL is set. Optional: LLM_VISION=1 (also send the
# iso renders), LLM_TIMEOUT (s per request, 60), LLM_TEMPERATURE (0), PLANNED_BUDGET (s per case for
# all of step 3, 75), PLANNED_CUT_TIMEOUT (s per boolean, 20).

CMD ["python", "scripts/main.py"]
