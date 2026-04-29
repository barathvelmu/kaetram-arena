#!/usr/bin/env bash
# Start the DeepSeek SSE-rewriting proxy on 127.0.0.1:8890.
#
# Same generic SSE rewriter as start-nim-proxy.sh (scripts/nim_proxy.py),
# but pointed at api.deepseek.com instead of NVIDIA NIM. Required because
# opencode's @ai-sdk/openai-compatible provider does not read DeepSeek's
# delta.reasoning_content field — without this proxy, V4-Pro/Flash CoT is
# billed but the text is dropped. See opencode issue #24097 for the
# upstream gap; the `interleaved.field` config directive is unimplemented
# in opencode 1.14.29 for this provider.
#
# Idempotent and concurrency-safe (same logic as start-nim-proxy.sh).
set -euo pipefail

for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${DEEPSEEK_PROXY_PORT:-8890}"
LOG="/tmp/deepseek_proxy.log"
PID_FILE="/tmp/deepseek_proxy.pid"

if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
  pid="$(cat "$PID_FILE" 2>/dev/null || echo "?")"
  echo "DeepSeek proxy already listening on 127.0.0.1:$PORT (pid $pid)"
  exit 0
fi

if [ -f "$PID_FILE" ]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && ! kill -0 "$old_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
  elif [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    for _ in $(seq 1 10); do
      if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
        echo "DeepSeek proxy bound by existing pid $old_pid on :$PORT"
        exit 0
      fi
      sleep 0.2
    done
    echo "ERROR: PID $old_pid is alive but port :$PORT not bound — refusing to spawn a duplicate." >&2
    exit 1
  fi
fi

cd "$PROJECT_DIR"
NIM_PROXY_PORT="$PORT" \
NIM_PROXY_UPSTREAM="https://api.deepseek.com" \
  nohup "$PROJECT_DIR/.venv/bin/python3" \
  "$PROJECT_DIR/scripts/nim_proxy.py" >> "$LOG" 2>&1 &
new_pid=$!
echo "$new_pid" > "$PID_FILE"

for _ in $(seq 1 20); do
  if ss -lnt "sport = :$PORT" | grep -q LISTEN; then
    echo "DeepSeek proxy listening on 127.0.0.1:$PORT → api.deepseek.com (pid $new_pid, log: $LOG)"
    exit 0
  fi
  sleep 0.25
done

kill "$new_pid" 2>/dev/null || true
rm -f "$PID_FILE"
echo "ERROR: DeepSeek proxy failed to bind on :$PORT — see $LOG" >&2
tail -20 "$LOG" >&2
exit 1
