#!/usr/bin/env bash
set -euo pipefail

# Restart OpenViking and the Vaka evolution proxy, verify both services, then
# run the Vaka batch train/eval pipeline.
#
# With no train/eval arguments this launcher runs the one-case smoke eval
# recommended by the Vaka Evolution local evaluation guide.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAKA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${VAKA_DIR}/../.." && pwd)"
source "${REPO_ROOT}/openviking/session/train/auto_commit.sh"
openviking_capture_train_launch_command "$@"

AUTO_COMMIT=false
DRY_RUN=false
OPENVIKING_PORT="${VAKA_OPENVIKING_PORT:-1933}"
PROXY_PORT="${VAKA_PROXY_PORT:-8765}"
WAIT_TIMEOUT_SECONDS="${VAKA_WAIT_TIMEOUT_SECONDS:-180}"
OPENVIKING_CONFIG_FILE="${VAKA_OPENVIKING_CONFIG_FILE:-${HOME}/.openviking/ov-eval-vaka.conf}"
EVOLUTION_ROOT="${EVOLUTION_ROOT:-${REPO_ROOT}/../evolution}"
RESULT_DIR_NAME="${VAKA_RESULT_DIR_NAME:-train}"
LOG_DIR="${REPO_ROOT}/result/vaka_dev_v1/service_logs"
OPENVIKING_LOG="${LOG_DIR}/openviking-server.log"
PROXY_LAUNCH_LOG="${LOG_DIR}/evolution-proxy-launch.log"
OPENVIKING_PID_FILE="${LOG_DIR}/openviking-server.pid"
declare -a TRAIN_CLI_ARGS=()

usage() {
  cat <<'USAGE'
Usage:
  bash benchmark/vaka/train/restart_vikingbot_train_eval.sh [launcher options] [train/eval args...]

Launcher options:
  --config PATH
      OpenViking config. Default: ~/.openviking/ov-eval-vaka.conf
  --evolution-root PATH
      evolution repository. Default: sibling ../evolution repository.
  --openviking-port N
      Local OpenViking port. Default: 1933.
  --proxy-port N
      Local evolution proxy port. Default: 8765.
  --wait-timeout N
      Service readiness timeout in seconds. Default: 180.
  --auto-commit
      Commit pending OpenViking changes before launching and attach run results
      to the commit as Git notes.
  --dry-run
      Print resolved services and the batch command without starting anything.
  -h, --help
      Show this help.

All remaining arguments are passed to benchmark/vaka/train/run_batch_train_eval.sh.

No train/eval arguments runs a one-case smoke eval:
  --epochs 0 --skip-baseline-eval --eval-split test --eval-index 19
  --trials 1 --batch-size 1 --concurrency 1

Examples:
  # One-case smoke eval
  export OV_API_KEY='<default/default user key>'
  bash benchmark/vaka/train/restart_vikingbot_train_eval.sh

  # Run one selected test case
  bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
    --epochs 0 --skip-baseline-eval --eval-split test \
    --eval-index 7 --trials 1 --batch-size 1 --concurrency 1 \
    --continue-on-rollout-failure --no-clean-result

  # Full 20-case eval
  bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
    --epochs 0 --skip-baseline-eval --eval-split test \
    --trials 1 --batch-size 20 --concurrency 10 \
    --continue-on-rollout-failure --no-clean-result

  # One train epoch followed by final test eval
  bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
    --epochs 1 --train-split train --train-trials 1 \
    --skip-baseline-eval --eval-split test --trials 1 \
    --batch-size 20 --concurrency 10 --commit-concurrency 10 \
    --continue-on-rollout-failure --no-clean-result
USAGE
}

log() {
  printf '[restart-vaka-train] %s\n' "$*"
}

fail() {
  printf '[restart-vaka-train] ERROR: %s\n' "$*" >&2
  exit 1
}

parse_launcher_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)
        [[ $# -ge 2 ]] || fail "--config requires a path"
        OPENVIKING_CONFIG_FILE="$2"
        shift 2
        ;;
      --config=*)
        OPENVIKING_CONFIG_FILE="${1#--config=}"
        shift
        ;;
      --evolution-root)
        [[ $# -ge 2 ]] || fail "--evolution-root requires a path"
        EVOLUTION_ROOT="$2"
        shift 2
        ;;
      --evolution-root=*)
        EVOLUTION_ROOT="${1#--evolution-root=}"
        shift
        ;;
      --openviking-port)
        [[ $# -ge 2 ]] || fail "--openviking-port requires a value"
        OPENVIKING_PORT="$2"
        shift 2
        ;;
      --openviking-port=*)
        OPENVIKING_PORT="${1#--openviking-port=}"
        shift
        ;;
      --proxy-port)
        [[ $# -ge 2 ]] || fail "--proxy-port requires a value"
        PROXY_PORT="$2"
        shift 2
        ;;
      --proxy-port=*)
        PROXY_PORT="${1#--proxy-port=}"
        shift
        ;;
      --wait-timeout)
        [[ $# -ge 2 ]] || fail "--wait-timeout requires a value"
        WAIT_TIMEOUT_SECONDS="$2"
        shift 2
        ;;
      --wait-timeout=*)
        WAIT_TIMEOUT_SECONDS="${1#--wait-timeout=}"
        shift
        ;;
      --auto-commit)
        AUTO_COMMIT=true
        shift
        ;;
      --dry-run)
        DRY_RUN=true
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        shift
        TRAIN_CLI_ARGS+=("$@")
        break
        ;;
      *)
        TRAIN_CLI_ARGS+=("$1")
        shift
        ;;
    esac
  done
}

validate_positive_integer() {
  local name="$1"
  local value="$2"
  if ! [[ "${value}" =~ ^[1-9][0-9]*$ ]]; then
    fail "${name} must be a positive integer, got: ${value}"
  fi
}

set_default_train_args() {
  if [[ ${#TRAIN_CLI_ARGS[@]} -gt 0 ]]; then
    return
  fi
  TRAIN_CLI_ARGS=(
    --epochs 0
    --skip-baseline-eval
    --eval-split test
    --eval-index 19
    --trials 1
    --batch-size 1
    --concurrency 1
    --continue-on-rollout-failure
    --no-clean-result
  )
}

find_openviking_server() {
  if [[ -n "${VAKA_OPENVIKING_SERVER_BIN:-}" ]]; then
    printf '%s' "${VAKA_OPENVIKING_SERVER_BIN}"
    return
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/openviking-server" ]]; then
    printf '%s' "${REPO_ROOT}/.venv/bin/openviking-server"
    return
  fi
  command -v openviking-server || true
}

find_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s' "${PYTHON_BIN}"
    return
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    printf '%s' "${REPO_ROOT}/.venv/bin/python"
    return
  fi
  command -v python3 || true
}

validate_setup() {
  [[ -f "${OPENVIKING_CONFIG_FILE}" ]] \
    || fail "OpenViking config not found: ${OPENVIKING_CONFIG_FILE}"
  [[ -x "${EVOLUTION_ROOT}/run.bash" ]] \
    || fail "evolution launcher not found or not executable: ${EVOLUTION_ROOT}/run.bash"
  [[ -x "${EVOLUTION_ROOT}/stop.bash" ]] \
    || fail "evolution stop script not found or not executable: ${EVOLUTION_ROOT}/stop.bash"
  [[ -f "${EVOLUTION_ROOT}/components/ov-benchmark-proxy/.env" ]] \
    || fail "evolution proxy .env not found"
  [[ -n "$(find_openviking_server)" ]] \
    || fail "openviking-server not found; run uv sync in ${REPO_ROOT}"
  [[ -n "$(find_python)" ]] || fail "Python not found"
  command -v curl >/dev/null 2>&1 || fail "curl is required"
  command -v lsof >/dev/null 2>&1 || fail "lsof is required"
  validate_workspace_alignment
}

read_proxy_env_value() {
  local variable_name="$1"
  (
    cd "${EVOLUTION_ROOT}/components/ov-benchmark-proxy"
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
    printf '%s' "${!variable_name:-}"
  )
}

paths_match() {
  local left="$1"
  local right="$2"
  local python_bin
  python_bin="$(find_python)"
  "${python_bin}" - "${left}" "${right}" <<'PY'
import sys
from pathlib import Path

left = Path(sys.argv[1]).expanduser().resolve()
right = Path(sys.argv[2]).expanduser().resolve()
raise SystemExit(0 if left == right else 1)
PY
}

validate_workspace_alignment() {
  local python_bin
  local workspace
  local proxy_memory_store
  local expected_memory_store
  local experience_enabled
  local experience_root
  local expected_experience_root
  python_bin="$(find_python)"
  workspace="$(
    "${python_bin}" - "${OPENVIKING_CONFIG_FILE}" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
config = json.loads(config_path.read_text(encoding="utf-8-sig"))
workspace = (config.get("storage") or {}).get("workspace")
if not workspace:
    raise SystemExit(f"storage.workspace is missing in {config_path}")
print(Path(workspace).expanduser())
PY
  )"

  proxy_memory_store="$(read_proxy_env_value OV_BENCHMARK_PROXY_MEMORY_STORE)"
  expected_memory_store="${workspace%/}/viking/default"
  if [[ -z "${proxy_memory_store}" ]] \
    || ! paths_match "${proxy_memory_store}" "${expected_memory_store}"; then
    fail "proxy memory store must match OpenViking workspace: expected ${expected_memory_store}, got ${proxy_memory_store:-<unset>}"
  fi

  experience_enabled="$(
    read_proxy_env_value OV_BENCHMARK_PROXY_EXPERIENCE_PROMPT_ENABLED \
      | tr '[:upper:]' '[:lower:]'
  )"
  if [[ "${experience_enabled}" == "1" || "${experience_enabled}" == "true" \
    || "${experience_enabled}" == "yes" || "${experience_enabled}" == "on" ]]; then
    experience_root="$(read_proxy_env_value OV_BENCHMARK_PROXY_EXPERIENCE_PROMPT_ROOT)"
    expected_experience_root="${expected_memory_store}/user/default/memories"
    if [[ -z "${experience_root}" ]] \
      || ! paths_match "${experience_root}" "${expected_experience_root}"; then
      fail "experience prompt root must match OpenViking workspace: expected ${expected_experience_root}, got ${experience_root:-<unset>}"
    fi
  fi
}

stop_listener() {
  local name="$1"
  local port="$2"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    log "no existing ${name} listener on port ${port}"
    return
  fi

  log "stopping ${name} listener(s) on port ${port}: ${pids}"
  kill ${pids} 2>/dev/null || true
  for _ in {1..20}; do
    if ! lsof -tiTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
      return
    fi
    sleep 0.2
  done

  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    log "force stopping ${name} listener(s): ${pids}"
    kill -9 ${pids} 2>/dev/null || true
  fi
}

json_matches() {
  local kind="$1"
  local python_bin
  python_bin="$(find_python)"
  "${python_bin}" -c '
import json
import sys

kind = sys.argv[1]
try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)

if kind == "openviking":
    ok = payload.get("status") == "ok" and payload.get("healthy") is True
elif kind == "proxy":
    ok = (
        payload.get("status") == "ok"
        and payload.get("rollout_ready") is True
        and not payload.get("rollout_configuration_errors")
        and payload.get("rollout_backend") == "MAIN_AGENT_STD"
        and payload.get("trace_backend") == "vaka_call_tos_only"
        and payload.get("judge_backend") == "home_test"
        and payload.get("case_loader_ready") is True
    )
elif kind == "cases":
    ok = bool(payload.get("cases"))
else:
    ok = False
raise SystemExit(0 if ok else 1)
' "${kind}"
}

wait_for_json() {
  local name="$1"
  local url="$2"
  local kind="$3"
  local log_file="$4"
  local deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
  local response=""

  log "waiting for ${name}: ${url}"
  while (( SECONDS < deadline )); do
    response="$(curl -fsS "${url}" 2>/dev/null || true)"
    if [[ -n "${response}" ]] && printf '%s' "${response}" | json_matches "${kind}"; then
      log "✓ ${name} is ready"
      return
    fi
    sleep 2
  done

  log "last ${name} response: ${response:-<empty>}"
  if [[ -f "${log_file}" ]]; then
    tail -80 "${log_file}" >&2 || true
  fi
  fail "${name} did not become ready within ${WAIT_TIMEOUT_SECONDS}s"
}

start_openviking() {
  local server_bin
  server_bin="$(find_openviking_server)"
  mkdir -p "${LOG_DIR}"
  : > "${OPENVIKING_LOG}"
  stop_listener "OpenViking" "${OPENVIKING_PORT}"

  log "starting OpenViking on port ${OPENVIKING_PORT}"
  log "OpenViking config: ${OPENVIKING_CONFIG_FILE}"
  log "OpenViking log: ${OPENVIKING_LOG}"
  (
    cd "${REPO_ROOT}"
    export OPENVIKING_CONFIG_FILE
    exec "${server_bin}" \
      --config "${OPENVIKING_CONFIG_FILE}" \
      --port "${OPENVIKING_PORT}"
  ) >"${OPENVIKING_LOG}" 2>&1 &
  echo "$!" > "${OPENVIKING_PID_FILE}"

  wait_for_json \
    "OpenViking" \
    "http://127.0.0.1:${OPENVIKING_PORT}/health" \
    "openviking" \
    "${OPENVIKING_LOG}"
}

start_proxy() {
  mkdir -p "${LOG_DIR}"
  : > "${PROXY_LAUNCH_LOG}"

  log "restarting evolution proxy on port ${PROXY_PORT}"
  log "evolution root: ${EVOLUTION_ROOT}"
  log "proxy launch log: ${PROXY_LAUNCH_LOG}"
  (
    cd "${EVOLUTION_ROOT}"
    export PROXY_PORT
    ./stop.bash
    ./run.bash
  ) >>"${PROXY_LAUNCH_LOG}" 2>&1 || {
    tail -100 "${PROXY_LAUNCH_LOG}" >&2 || true
    fail "evolution proxy failed to start"
  }

  wait_for_json \
    "evolution proxy" \
    "http://127.0.0.1:${PROXY_PORT}/inspect/healthz" \
    "proxy" \
    "${EVOLUTION_ROOT}/temp/ov-benchmark-proxy/proxy.log"
}

check_case_loader() {
  local response
  response="$(
    curl -fsS -X POST "http://127.0.0.1:${PROXY_PORT}/v1/cases/query" \
      -H 'Content-Type: application/json' \
      -d '{"dataset":"vaka_dev_v1","domain":"benchmark","split":"test","limit":1}'
  )"
  if ! printf '%s' "${response}" | json_matches "cases"; then
    fail "Vaka case loader returned no test cases: ${response}"
  fi
  log "✓ Vaka test cases are available"
}

has_train_option() {
  local target="$1"
  local argument
  for argument in "${TRAIN_CLI_ARGS[@]}"; do
    case "${argument}" in
      "${target}"|"${target}"=*)
        return 0
        ;;
    esac
  done
  return 1
}

format_train_args_redacted() {
  local -a redacted=()
  local argument
  set -- "${TRAIN_CLI_ARGS[@]}"
  while [[ $# -gt 0 ]]; do
    argument="$1"
    case "${argument}" in
      --api-key|--access-token|--auth-token|--password|--secret|--token)
        redacted+=("${argument}" "***")
        shift
        if [[ $# -gt 0 ]]; then
          shift
        fi
        ;;
      --api-key=*|--access-token=*|--auth-token=*|--password=*|--secret=*|--token=*)
        redacted+=("${argument%%=*}=***")
        shift
        ;;
      *)
        redacted+=("${argument}")
        shift
        ;;
    esac
  done
  printf '%s' "${redacted[*]}"
}

print_plan() {
  local redacted_train_args
  redacted_train_args="$(format_train_args_redacted)"
  log "dry-run: no services will be changed"
  log "OpenViking: $(find_openviking_server) --config ${OPENVIKING_CONFIG_FILE} --port ${OPENVIKING_PORT}"
  log "evolution: PROXY_PORT=${PROXY_PORT} ${EVOLUTION_ROOT}/run.bash"
  log "batch: benchmark/vaka/train/run_batch_train_eval.sh --benchmark-service-url http://127.0.0.1:${PROXY_PORT} --config ${OPENVIKING_CONFIG_FILE} --server-url http://127.0.0.1:${OPENVIKING_PORT} --result-dir-name ${RESULT_DIR_NAME} ${redacted_train_args}"
}

run_train_eval() {
  local -a batch_args=(
    --benchmark-service-url "http://127.0.0.1:${PROXY_PORT}"
    --config "${OPENVIKING_CONFIG_FILE}"
    --server-url "http://127.0.0.1:${OPENVIKING_PORT}"
    --result-dir-name "${RESULT_DIR_NAME}"
  )
  if ! has_train_option "--api-key" && [[ -n "${OV_API_KEY:-}" ]]; then
    batch_args+=(--api-key "${OV_API_KEY}")
  fi
  batch_args+=("${TRAIN_CLI_ARGS[@]}")

  export OPENVIKING_CONFIG_FILE
  log "starting Vaka batch train/eval"
  log "results: ${REPO_ROOT}/result/vaka_dev_v1/${RESULT_DIR_NAME}"
  cd "${REPO_ROOT}"
  exec benchmark/vaka/train/run_batch_train_eval.sh "${batch_args[@]}"
}

main() {
  parse_launcher_args "$@"
  validate_positive_integer "--openviking-port" "${OPENVIKING_PORT}"
  validate_positive_integer "--proxy-port" "${PROXY_PORT}"
  validate_positive_integer "--wait-timeout" "${WAIT_TIMEOUT_SECONDS}"
  set_default_train_args

  if [[ "${DRY_RUN}" == "true" ]]; then
    print_plan
    return
  fi

  validate_setup
  openviking_train_auto_commit "${REPO_ROOT}" "vaka train eval"
  start_openviking
  start_proxy
  check_case_loader
  run_train_eval
}

main "$@"
