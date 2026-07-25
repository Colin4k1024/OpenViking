#!/usr/bin/env bash
set -euo pipefail

# Vaka convenience launcher for the generic OpenViking session/train batch pipeline.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

exec "${REPO_ROOT}/openviking/session/train/run_batch_train_eval.sh" \
  --dataset vaka_dev_v1 \
  --domain benchmark \
  --concurrency 10 \
  --commit-concurrency 10 \
  "$@"
