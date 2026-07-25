from __future__ import annotations

import sys
from pathlib import Path

from openviking.session.train.batch_runner import BatchTrainEvalConfig
from openviking.session.train.run_batch_train_eval import parse_args

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_train_concurrency_defaults_to_150(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_batch_train_eval", "--dataset", "tau2", "--domain", "airline"],
    )

    args = parse_args()
    config = BatchTrainEvalConfig(dataset="tau2", domain="airline")

    assert args.concurrency == 150
    assert args.commit_concurrency == 150
    assert config.concurrency == 150
    assert config.commit_concurrency == 150


def test_train_concurrency_explicit_overrides_are_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_train_eval",
            "--dataset",
            "tau2",
            "--domain",
            "airline",
            "--concurrency",
            "80",
            "--commit-concurrency",
            "90",
        ],
    )

    args = parse_args()
    config = BatchTrainEvalConfig(
        dataset="tau2",
        domain="airline",
        concurrency=80,
        commit_concurrency=90,
    )

    assert args.concurrency == 80
    assert args.commit_concurrency == 90
    assert config.concurrency == 80
    assert config.commit_concurrency == 90


def test_tau2_launchers_use_concurrency_150() -> None:
    launcher = (REPO_ROOT / "benchmark/tau2/train/run_batch_train_eval.sh").read_text()
    restart_launcher = (
        REPO_ROOT / "benchmark/tau2/train/restart_vikingbot_train_eval.sh"
    ).read_text()

    assert "--concurrency 150" in launcher
    assert "--commit-concurrency 150" in launcher
    assert "--commit-concurrency 150" in restart_launcher


def test_no_eval_each_epoch_overrides_tau2_wrapper_default(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_batch_train_eval",
            "--dataset",
            "tau2",
            "--domain",
            "airline",
            "--eval-each-epoch",
            "--no-eval-each-epoch",
        ],
    )

    args = parse_args()

    assert args.eval_each_epoch is False
    assert args.skip_final_eval is False
