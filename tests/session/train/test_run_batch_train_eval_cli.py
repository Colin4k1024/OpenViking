from __future__ import annotations

import sys

from openviking.session.train.run_batch_train_eval import parse_args


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
