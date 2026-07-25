# OpenViking Install

This file is for coding agents. It installs and starts a local OpenViking server on the user's machine. OpenViking installs from PyPI — you do not need this repository cloned. Human readers: see [README.md](./README.md).

## Goal

Get a working OpenViking server running locally with the least risky path, reachable at `http://127.0.0.1:1933`, ready for agents and SDK clients to connect.

Default preference:

1. Direct path — PyPI install, config written from values the user supplies through your question UI
2. Interactive wizard `openviking-server init` — fallback only, when the direct path fails

## Required Inputs

Collect these up front with your structured question / option-select UI. Do not guess values, and do not scrape them from dotfiles or shell history.

| Input | When | How to ask |
| --- | --- | --- |
| Model provider | always | Option-select from the provider table in Step 4 |
| Provider API key | unless Ollama | Free-text; one Ark key covers both models for Volcengine |

## Operating Rules

- Be idempotent. Re-running this document must not damage an existing setup.
- Prefer writing `~/.openviking/ov.conf` directly from user-supplied values. The interactive `openviking-server init` wizard is the fallback, not the default.
- Never overwrite an existing `~/.openviking/ov.conf`. If one exists, validate it with `openviking-server doctor` instead of replacing it.
- Do not use `sudo` or install system packages without explicit user approval.
- Do not print, echo, or log API keys.
- If a step fails, stop, report the blocker, and propose the smallest next action.

## Success Criteria

- `openviking-server doctor` passes.
- `curl -fsS http://127.0.0.1:1933/health` succeeds.
- The user has been told: the server URL, the Studio URL (`http://127.0.0.1:1933/studio`), the log path, and how to stop the server (`pkill openviking; pkill agfs`).

## Steps

### 1. Preflight

```bash
python3 --version   # need >= 3.10
```

- Python missing or < 3.10 → stop and report; do not install Python without approval.
- For the Volcengine provider prefer Python 3.11–3.13: on 3.14 the Ark SDK emits noisy Pydantic warnings (works, but ugly).

### 2. Existing state

```bash
curl -fsS http://127.0.0.1:1933/health
```

- Succeeds → a server is already running. Stop and report; nothing to install.
- `~/.openviking/ov.conf` exists but no server running → skip to Step 5 (validate, then start).

### 3. Install

```bash
pip install openviking --upgrade
```

(`uv tool install openviking --upgrade` is an acceptable alternative when `uv` is present.)

### 4. Configure

Ask the user via option-select: which model provider? Then ask for its API key (skip for Ollama).

| Provider option | `embedding.dense` values | `vlm` values |
| --- | --- | --- |
| Volcengine Ark (default) | model `doubao-embedding-vision-251215`, api_base `https://ark.cn-beijing.volces.com/api/v3`, dimension `1024`, input `multimodal` | model `doubao-seed-2-0-lite-260428`, same api_base |
| OpenAI | model `text-embedding-3-small`, api_base `https://api.openai.com/v1`, dimension `1536` | model `gpt-5.4`, same api_base |
| Ollama (local, no key) | model `nomic-embed-text`, api_base `http://localhost:11434/v1`, dimension `768`, input `text` | not provided — combine with another VLM provider |
| Other (Codex OAuth, Kimi, GLM, custom) | — | Use the Fallback below — the wizard handles these |

Write `~/.openviking/ov.conf` (Volcengine values shown — substitute from the table):

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "api_key": "<EMBEDDING_API_KEY>",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-2-0-lite-260428",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "api_key": "<VLM_API_KEY>"
  }
}
```

### 5. Validate

```bash
openviking-server doctor
```

If `doctor` fails and its output does not point to a fix you can apply confidently, use the Fallback below instead of guessing.

### 6. Start and verify

```bash
mkdir -p ~/.openviking/data/log
nohup openviking-server > ~/.openviking/data/log/openviking.log 2>&1 &
curl -fsS http://127.0.0.1:1933/health
```

Report to the user: server at `http://127.0.0.1:1933`, Studio at `http://127.0.0.1:1933/studio`, logs at `~/.openviking/data/log/openviking.log`, stop with `pkill openviking; pkill agfs`.

## Fallback: interactive wizard

Use `openviking-server init` when the direct path cannot finish:

- the user's provider is not in the Step 4 table (Codex OAuth needs a browser sign-in the wizard drives),
- `doctor` fails and you cannot resolve it from its output,
- or the user asks for guided setup.

The wizard is interactive — hand it to the user: tell them to run `openviking-server init` in their own terminal and confirm when it completes. Then resume from Step 5 (validate → start → verify). Do not attempt to script or pipe input into the wizard.

## TODO

Copy this list into your own todo tracking and check items off as you go:

- [ ] Preflight passed: Python >= 3.10
- [ ] No server already running; existing `ov.conf` respected
- [ ] `pip install openviking --upgrade`
- [ ] Provider + API key collected via option prompt
- [ ] `~/.openviking/ov.conf` written (or wizard fallback completed by the user)
- [ ] `openviking-server doctor` passed
- [ ] Server started; `/health` succeeds
- [ ] URLs, log path, and stop command reported to the user

## EXECUTE NOW

Work through Steps 1–6. Stop when the Success Criteria are met and report status. Do not add resources, index content, create accounts, or install agent plugins unless the user asks — for connecting a coding agent, point them to `examples/openclaw-plugin/INSTALL.md` or `examples/opencode-plugin/INSTALL.md`.
