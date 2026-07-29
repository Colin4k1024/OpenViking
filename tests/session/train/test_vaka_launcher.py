# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = REPO_ROOT / "benchmark/vaka/train/restart_vikingbot_train_eval.sh"


def test_vaka_launcher_dry_run_uses_safe_smoke_defaults():
    completed = subprocess.run(
        ["bash", str(LAUNCHER), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )

    output = completed.stdout
    assert "dry-run: no services will be changed" in output
    assert "benchmark/vaka/train/run_batch_train_eval.sh" in output
    assert "--epochs 0" in output
    assert "--eval-split test" in output
    assert "--eval-index 19" in output
    assert "--trials 1" in output
    assert "--concurrency 1" in output


def test_vaka_launcher_dry_run_redacts_forwarded_api_key():
    completed = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "--dry-run",
            "--epochs",
            "1",
            "--api-key",
            "top-secret",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--api-key ***" in completed.stdout
    assert "top-secret" not in completed.stdout


def test_vaka_launcher_forwards_selected_eval_index():
    completed = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "--dry-run",
            "--epochs",
            "0",
            "--skip-baseline-eval",
            "--eval-split",
            "test",
            "--eval-index",
            "7",
            "--trials",
            "1",
            "--batch-size",
            "1",
            "--concurrency",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    output = completed.stdout
    assert "--epochs 0" in output
    assert "--eval-index 7" in output
    assert "--batch-size 1" in output
    assert "--concurrency 1" in output


def test_vaka_launcher_runs_batch_without_api_key_under_nounset():
    shell = r"""
launcher="$1"
set -- --dry-run
source "$launcher"

exec() {
  printf 'arg=%s\n' "$@"
}

unset OV_API_KEY
TRAIN_CLI_ARGS=(--epochs 0)
run_train_eval
"""
    completed = subprocess.run(
        ["bash", "-u", "-c", shell, "bash", str(LAUNCHER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "arg=benchmark/vaka/train/run_batch_train_eval.sh" in completed.stdout
    assert "arg=--epochs" in completed.stdout
    assert "arg=0" in completed.stdout
