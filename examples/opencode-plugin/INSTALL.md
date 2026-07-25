# OpenViking OpenCode Plugin Install

This file is for coding agents. It installs and configures `@openviking/opencode-plugin`, which gives OpenCode long-term memory, automatic recall, and OpenViking MCP tools. Human readers: see [README.md](./README.md).

## Goal

Connect an existing OpenCode installation to a running OpenViking server through `@openviking/opencode-plugin`.

Default preference:

1. Package install — add the plugin to `~/.config/opencode/opencode.json`
2. Source install — only for development, debugging, or PR testing from a repo checkout

## Required Inputs

Collect these up front with your structured question / option-select UI. Do not guess values, and do not scrape them from dotfiles or shell history.

| Input | When | How to ask |
| --- | --- | --- |
| OpenViking base URL | always | Offer options: `http://127.0.0.1:1933` (local default) / a remote URL the user types / "no server yet — set one up locally" |
| OpenViking API key | when server auth is enabled | Free-text; will be set as `OPENVIKING_API_KEY` |
| Model provider + API key | only for "no server yet" | See Step 2 |

## Operating Rules

- Be idempotent. Re-running this document must not damage an existing setup.
- Do not run the interactive `openviking-server init` wizard. Collect parameters through your question UI and write config files directly.
- Do not overwrite an existing `openviking-config.json` or `ov.conf`; if one exists, stop and ask.
- Put the API key in the `OPENVIKING_API_KEY` environment variable, not in config files.
- Do not use `sudo` or install system packages without explicit user approval.
- Do not print, echo, or log API keys.
- If a step fails, stop, report the blocker, and propose the smallest next action.

## Success Criteria

- `curl -fsS <BASE_URL>/health` succeeds.
- The plugin entry exists in OpenCode config (package install) or the plugin files exist under `~/.config/opencode/plugins/` (source install).
- After an OpenCode restart, a session exposes the `openviking_*` MCP tools (e.g. `openviking_recall`, `openviking_search`, `openviking_health`).
- The user knows what changed and that OpenCode must be restarted.

## Steps

### 1. Preflight

```bash
node -v   # need >= 18
```

OpenCode itself must already be installed; if not, stop and tell the user.

### 2. Server

Probe the base URL collected in Required Inputs:

```bash
curl -fsS <BASE_URL>/health
```

- Reachable → go to Step 3.
- Remote URL unreachable → stop and report.
- User chose "no server yet" → set up a local server. Do not run `openviking-server init`; instead:

  1. `pip install openviking --upgrade`
  2. Ask the user via option-select: which model provider? Then ask for its API key (skip for Ollama).

     | Provider option | `embedding.dense` values | `vlm` values |
     | --- | --- | --- |
     | Volcengine Ark (default) | model `doubao-embedding-vision-251215`, api_base `https://ark.cn-beijing.volces.com/api/v3`, dimension `1024`, input `multimodal` | model `doubao-seed-2-0-lite-260428`, same api_base |
     | OpenAI | model `text-embedding-3-small`, api_base `https://api.openai.com/v1`, dimension `1536` | model `gpt-5.4`, same api_base |
     | Ollama (local, no key) | model `nomic-embed-text`, api_base `http://localhost:11434/v1`, dimension `768`, input `text` | not provided — combine with another VLM provider |

     Other providers (Codex OAuth, Kimi, GLM): see `docs/en/guides/01-configuration.md`. Codex OAuth needs an interactive sign-in — hand that to the user instead of automating it.
  3. Write `~/.openviking/ov.conf` (stop and ask if it exists). Template (Volcengine values shown — substitute from the table):

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

  4. Validate, start in the background, and probe:

     ```bash
     openviking-server doctor
     mkdir -p ~/.openviking/data/log
     nohup openviking-server > ~/.openviking/data/log/openviking.log 2>&1 &
     curl -fsS http://127.0.0.1:1933/health
     ```

     If `doctor` fails, report its output and stop — do not start the server.

### 3. Install the plugin

**Package install (default).** Merge into `~/.config/opencode/opencode.json` (create if missing, preserve existing entries):

```json
{
  "plugin": ["@openviking/opencode-plugin"]
}
```

**Source install (only when the user asks for dev/PR testing).** From the repository root:

```bash
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

The top-level `openviking.js` wrapper is what OpenCode's local plugin scanner discovers; package installs do not need it.

### 4. Configure

If the user provided an API key, export it where OpenCode runs (shell profile or session environment):

```bash
export OPENVIKING_API_KEY="<API_KEY>"
```

Plugin behavior defaults are sane. Only write `~/.config/opencode/openviking-config.json` when the user wants non-default behavior (recall limits, token budgets, base URL other than `http://127.0.0.1:1933`):

```json
{
  "enabled": true,
  "autoRecall": { "enabled": true, "limit": 6, "scoreThreshold": 0.35 }
}
```

`OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_PEER_ID` env vars take precedence over config-file values. `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` are only for trusted-mode deployments — leave them unset in API-key mode.

### 5. Verify

Tell the user to restart OpenCode, then in a new session confirm the `openviking_*` MCP tools are available (for example by calling `openviking_health`).

If something looks wrong, check in this order:

| Symptom | Check |
| --- | --- |
| Plugin does not load | Package install: `opencode.json` contains `@openviking/opencode-plugin`. Source install: `~/.config/opencode/plugins/openviking.js` exists |
| Tools hit the wrong server | `~/.openviking/ovcli.conf`, `OPENVIKING_*` env vars, or `OPENVIKING_PLUGIN_CONFIG` |
| 401 / 403 | `OPENVIKING_API_KEY`; for trusted mode also `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |
| Recall empty | Server has indexed content; `autoRecall.enabled` is `true` |
| Runtime logs | `tail -n 100 ~/.config/opencode/openviking/openviking-memory.log` |

## TODO

Copy this list into your own todo tracking and check items off as you go:

- [ ] Collect base URL and API key from the user via option prompt
- [ ] Preflight passed: Node >= 18, OpenCode present
- [ ] Server `/health` reachable (local server set up first if the user chose that)
- [ ] Plugin added to `opencode.json` (or source files copied)
- [ ] `OPENVIKING_API_KEY` exported (when auth is enabled)
- [ ] User told to restart OpenCode; `openviking_*` tools confirmed in a new session
- [ ] Result reported to the user

## EXECUTE NOW

Work through Steps 1–5. Stop when the Success Criteria are met and report status. Do not start indexing content, add resources, or change other OpenCode plugins unless the user asks.
