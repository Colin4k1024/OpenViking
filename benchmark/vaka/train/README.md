# Vaka train/eval

This launcher follows the local Vaka Evolution evaluation guide. It restarts:

1. OpenViking on port `1933`.
2. The sibling `evolution` repository's benchmark proxy on port `8765`.
3. The generic OpenViking batch train/eval pipeline with dataset `vaka_dev_v1`.

Complete the one-time setup first:

- Create `~/.openviking/ov-eval-vaka.conf`.
- Configure `evolution/components/ov-benchmark-proxy/.env`.
- Install the OpenViking, evolution proxy, and home_test environments.
- Put `vaka-dev-package`, the train/eval scoring templates, the Vaka user pool,
  and the required private TOS/home_test configuration in the paths referenced
  by the proxy `.env`.

Run the recommended one-case smoke eval:

```bash
export OV_API_KEY='<default/default user key>'
bash benchmark/vaka/train/restart_vikingbot_train_eval.sh
```

Run a different test index:

```bash
bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
  --epochs 0 \
  --skip-baseline-eval \
  --eval-split test \
  --eval-index 7 \
  --trials 1 \
  --batch-size 1 \
  --concurrency 1 \
  --continue-on-rollout-failure \
  --no-clean-result
```

Run the full 20-case eval:

```bash
bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
  --epochs 0 \
  --skip-baseline-eval \
  --eval-split test \
  --trials 1 \
  --batch-size 20 \
  --concurrency 10 \
  --continue-on-rollout-failure \
  --no-clean-result
```

Run one train epoch followed by final test eval:

```bash
bash benchmark/vaka/train/restart_vikingbot_train_eval.sh \
  --epochs 1 \
  --train-split train \
  --train-trials 1 \
  --skip-baseline-eval \
  --eval-split test \
  --trials 1 \
  --batch-size 20 \
  --concurrency 10 \
  --commit-concurrency 10 \
  --continue-on-rollout-failure \
  --no-clean-result
```

Use `--dry-run` to inspect the resolved services and default batch arguments
without stopping or starting any process. The proxy mode—Baseline, Experience
Prompt, or local Memory—is controlled by the evolution proxy `.env`, as
described in the setup guide.
