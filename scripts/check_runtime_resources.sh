#!/usr/bin/env bash
set -Eeuo pipefail

DEVICE="${1:-}"
PORT="${2:-}"
PYTHON_BIN="${PYTHON_BIN:-python}"

fail() { echo "ERROR: $*" >&2; exit 2; }

[[ "$DEVICE" =~ ^[0-9]+$ ]] || fail "DEVICE must be a non-negative integer: '$DEVICE'"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "PORT must be an integer: '$PORT'"
(( PORT >= 1 && PORT <= 65535 )) || fail "PORT is outside the valid range 1..65535: $PORT"

command -v npu-smi >/dev/null 2>&1 || fail "npu-smi is unavailable; cannot verify DEVICE=$DEVICE"
health_output="$(npu-smi info -t health -i "$DEVICE" 2>&1)" || \
  fail "DEVICE=$DEVICE is unavailable or cannot be queried with npu-smi"
grep -Eq "NPU ID[[:space:]]*:[[:space:]]*$DEVICE([[:space:]]|$)" <<<"$health_output" || \
  fail "DEVICE=$DEVICE was not found by npu-smi"
grep -Eq "Health[[:space:]]*:[[:space:]]*OK" <<<"$health_output" || \
  fail "DEVICE=$DEVICE is not healthy"

if ! "$PYTHON_BIN" - "$PORT" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit(1)
finally:
    sock.close()
PY
then
  fail "PORT=$PORT is already in use or cannot be bound"
fi

echo "resources_ok device=$DEVICE port=$PORT"
