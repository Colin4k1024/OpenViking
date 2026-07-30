# LoCoMo `ov compile` memory experiment

This experiment keeps the existing VikingBot eval and judge stages unchanged.
Only the import stage is replaced.

## Isolation

Start OpenViking with a fresh data/profile root for each experiment. Keep the
LoCoMo peer IDs unchanged (`conv-26`, `conv-30`, and so on), so `run_eval.py`
continues to resolve the correct peer store without a mapping layer.

Do not reuse a store populated by `import_to_ov.py` unless mixing those memories
is intentional.

## Preview the batches

```bash
python benchmark/locomo/vikingbot/import_via_compile.py --dry-run
```

The default batch size is 100 utterances per OpenViking session, and the default
Compile group contains two adjacent sessions. The final group may contain fewer
sessions. Sessions never cross a LoCoMo conversation boundary.

## Import through Compile

Build/install the modified `ov` binary and ensure the selected CLI config points
to an OpenViking server started with VikingBot:

```bash
uv run python benchmark/locomo/vikingbot/import_via_compile.py \
  --run-id dream-v1 \
  --batch-size 100 \
  --sessions-per-compile 2 \
  --concurrency 10 \
  --reason "Prefer durable facts supported directly by the conversation."
```

Different LoCoMo conversations are processed concurrently (10 by default).
Compile groups within one conversation remain serial because each group reads
the memories produced by the preceding group. Use `--concurrency` to lower the
number of conversations in flight when needed.

Use `--sessions-per-compile` to change how many adjacent sessions are supplied
to one Compile task. It defaults to 2 and accepts 1 through 15.

Use `--no-default-instruction` without `--reason` to measure the extractor with
no additional Compile-provided instruction. If `--reason` is present, the
explicit instruction is still used.

```bash
uv run python benchmark/locomo/vikingbot/import_via_compile.py \
  --run-id dream-no-extra-instruction \
  --batch-size 100 \
  --no-default-instruction
```

Select one or more samples while iterating:

```bash
uv run python benchmark/locomo/vikingbot/import_via_compile.py \
  --run-id dream-smoke \
  --sample conv-26
```

The script checkpoints every completed Compile group under
`benchmark/locomo/vikingbot/result/`. Reusing the same run ID resumes completed
groups; choose a new run ID for a clean experiment.

For every full group it runs the equivalent of:

```bash
ov compile \
  --from viking://user/default/sessions/<batch-session-1> \
  --from viking://user/default/sessions/<batch-session-2> \
  --from viking://user/default/peers/conv-26/memories \
  --to viking://user/default/peers/conv-26/memories \
  --wait
```

No `--skill` is supplied. Compile therefore invokes the ordinary
`profile/preferences/entities/events` extract loop and updates the same peer
memory store in place.

## Eval and judge

After all batches finish, reuse the existing pipeline and skip its normal
import stage:

```bash
benchmark/locomo/vikingbot/run_full_eval.sh --skip-import
```

This runs the existing retrieval/eval, judge, and statistics stages against
the Compile-produced peer memories.
