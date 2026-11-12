#!/usr/bin/env bash
# Starts the vLLM server(s) on the Brev GPU box, each in its own tmux session.
#
# Default: one multimodal model (Gemma 4 31B FP8) on the whole GPU; it reads the views itself,
# so no separate vision model runs. Set VLM_MODEL to serve a second model next to it, and lower
# LLM_GPU_UTIL so both fit, e.g. the earlier Qwen pair:
#   LLM_MODEL=Qwen/Qwen3-30B-A3B-FP8 LLM_GPU_UTIL=0.55 \
#   LLM_EXTRA_ARGS="--enable-auto-tool-choice --tool-call-parser hermes" \
#   VLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct scripts/serve_models.sh
#
#   scripts/serve_models.sh            # start (no-op for servers already running)
#   scripts/serve_models.sh status     # show health of the servers
#   scripts/serve_models.sh stop       # stop the servers
#   scripts/serve_models.sh restart
#
# Each server exposes an OpenAI-compatible API:
#   LLM  http://localhost:${LLM_PORT}/v1
#   VLM  http://localhost:${VLM_PORT}/v1   (only when VLM_MODEL is set)
# Override any setting below via env vars, e.g. LLM_MODEL=... scripts/serve_models.sh
set -euo pipefail

# The BF16 google/gemma-4-31B-it takes 59 GB and leaves too little KV cache for a 32K context
# on one H100 80 GB; the FP8 build (32 GB) fits.
LLM_MODEL="${LLM_MODEL:-RedHatAI/gemma-4-31B-it-FP8-block}"
LLM_PORT="${LLM_PORT:-8000}"
LLM_GPU_UTIL="${LLM_GPU_UTIL:-0.90}"
# Extra `vllm serve` flags for the LLM, as one shell-quoted string.
LLM_EXTRA_ARGS="${LLM_EXTRA_ARGS-"--limit-mm-per-prompt '{\"image\": 10, \"video\": 0}'"}"

VLM_MODEL="${VLM_MODEL:-}" # empty = no second server
VLM_PORT="${VLM_PORT:-8001}"
VLM_GPU_UTIL="${VLM_GPU_UTIL:-0.33}"

MAX_MODEL_LEN="${MAX_MODEL_LEN:-32768}"
LOG_DIR="${LOG_DIR:-$HOME/logs}"
READY_TIMEOUT="${READY_TIMEOUT:-900}" # seconds; first run downloads weights

export PATH="$HOME/.local/bin:$PATH"

ensure_deps() {
  command -v tmux >/dev/null || sudo apt-get install -y -qq tmux
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  # flashinfer JIT-compiles kernels at startup and needs ninja on PATH;
  # ninja-build isn't in this image's apt sources, so take it from pip.
  if ! command -v vllm >/dev/null || ! command -v ninja >/dev/null; then
    uv tool install vllm --with ninja
    sudo ln -sf "$HOME/.local/share/uv/tools/vllm/bin/ninja" /usr/local/bin/ninja
  fi
  mkdir -p "$LOG_DIR"
}

is_healthy() { curl -sf -o /dev/null "http://localhost:$1/v1/models"; }

wait_ready() { # name port
  local name=$1 port=$2 waited=0
  printf 'waiting for %s on :%s ' "$name" "$port"
  until is_healthy "$port"; do
    if ! tmux has-session -t "$name" 2>/dev/null; then
      echo; echo "ERROR: $name exited during startup. Last log lines:" >&2
      tail -n 30 "$LOG_DIR/$name.log" >&2
      exit 1
    fi
    if (( waited >= READY_TIMEOUT )); then
      echo; echo "ERROR: $name not ready after ${READY_TIMEOUT}s (see $LOG_DIR/$name.log)" >&2
      exit 1
    fi
    printf '.'; sleep 5; waited=$((waited + 5))
  done
  echo " ready"
}

start_one() { # name port model gpu_util [extra vllm args, one shell-quoted string]
  local name=$1 port=$2 model=$3 util=$4 extra=${5:-}
  if is_healthy "$port"; then
    echo "$name already running on :$port"
    return
  fi
  tmux kill-session -t "$name" 2>/dev/null || true
  tmux new-session -d -s "$name" \
    "vllm serve $model --port $port --gpu-memory-utilization $util --max-model-len $MAX_MODEL_LEN $extra 2>&1 | tee $LOG_DIR/$name.log"
  wait_ready "$name" "$port"
}

start() {
  ensure_deps
  # Start sequentially: vLLM sizes its KV cache by profiling free GPU memory,
  # and two servers profiling at once miscount each other's usage.
  start_one llm "$LLM_PORT" "$LLM_MODEL" "$LLM_GPU_UTIL" "$LLM_EXTRA_ARGS"
  if [[ -n "$VLM_MODEL" ]]; then
    start_one vlm "$VLM_PORT" "$VLM_MODEL" "$VLM_GPU_UTIL"
  fi
  status
}

stop() {
  for name in llm vlm; do
    tmux kill-session -t "$name" 2>/dev/null && echo "stopped $name" || echo "$name not running"
  done
}

status() {
  for pair in "llm:$LLM_PORT:$LLM_MODEL" "vlm:$VLM_PORT:$VLM_MODEL"; do
    IFS=: read -r name port model <<<"$pair"
    [[ -n "$model" ]] || continue
    if is_healthy "$port"; then s=UP; else s=DOWN; fi
    printf '%-4s %-5s http://localhost:%s/v1  %s\n' "$name" "$s" "$port" "$model"
  done
  command -v nvidia-smi >/dev/null &&
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/gpu  /'
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 3; start ;;
  status) status ;;
  *) echo "usage: $0 [start|stop|restart|status]" >&2; exit 2 ;;
esac
