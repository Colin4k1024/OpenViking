# OpenViking OpenClaw Plugin Install

This file is for coding agents. It installs and configures `@openviking/openclaw-plugin`, the OpenViking context-engine plugin for OpenClaw. Human readers: see [README.md](./README.md).

## Goal

Connect an existing OpenClaw installation to a running OpenViking server through `@openviking/openclaw-plugin`, taking the least risky path.

Default preference:

1. ClawHub package install: `openclaw plugins install clawhub:@openviking/openclaw-plugin`
2. Backup path `openclaw-openviking-setup-helper` — only when ClawHub is unreachable or rate-limited, or the user explicitly asks for a source ref

Never run `clawhub install openviking` — that installs an AgentSkill named `openviking`, not this plugin.

## Required Inputs

Collect these up front with your structured question / option-select UI. Do not guess values, and do not scrape them from dotfiles or shell history.

| Input | When | How to ask |
| --- | --- | --- |
| OpenViking base URL | always | Offer options: `http://127.0.0.1:1933` (local default) / a remote URL the user types / "no server yet — set one up locally" |
| OpenViking API key | always | Free-text; empty is valid only when the server runs without auth |
| Model provider + API key | only for "no server yet" | See Step 2 |
| Account ID + User ID | only if setup reports `keyProbe.keyType: "root_key"` | Free-text |
| Slot replacement approval | only if setup reports `action: "slot_blocked"` | Yes/No; default No |

## Operating Rules

- Be idempotent. Re-running this document must not damage an existing setup.
- Pass `--json` to every `openclaw openviking` command and branch on fields; never parse human-oriented output.
- Do not run interactive wizards (`openclaw openviking setup` without flags, `openviking-server init`). Collect parameters through your question UI, then pass flags or write config files directly.
- Never replace another context engine without explicit user approval.
- Do not use `sudo` or install system packages without explicit user approval.
- Do not print, echo, or log API keys.
- If a step fails, stop, report the blocker, and propose the smallest next action.

## Success Criteria

- `openclaw openviking status --json` returns `configured: true` and `slotActive: true`.
- `health.ok` is `true` — unless the user explicitly approved an offline config save.
- The user knows what changed and what to do next.

## Steps

### 1. Preflight

```bash
node -v            # need >= 22
openclaw --version # need >= 2026.5.27
```

- OpenClaw missing → stop and tell the user to run `npm install -g openclaw && openclaw onboard` first.
- OpenClaw older than 2026.5.27 → stop and report; do not work around. (2026.5.4+ refuses TypeScript-source plugins; the published package ships compiled `dist/*.js`.)
- Multiple OpenClaw state directories → ask which one, then pass `--workdir <path>` consistently.

### 2. Server

Probe the base URL collected in Required Inputs:

```bash
curl -fsS <BASE_URL>/health
```

- Reachable → go to Step 3.
- Remote URL unreachable → stop and report; the plugin cannot work without a server.
- User chose "no server yet" → set up a local server. Do not run `openviking-server init` (interactive wizard); instead:

  1. `pip install openviking --upgrade`
  2. Ask the user via option-select: which model provider? Then ask for its API key (skip for Ollama).

     | Provider option | `embedding.dense` values | `vlm` values |
     | --- | --- | --- |
     | Volcengine Ark (default) | model `doubao-embedding-vision-251215`, api_base `https://ark.cn-beijing.volces.com/api/v3`, dimension `1024`, input `multimodal` | model `doubao-seed-2-0-lite-260428`, same api_base |
     | OpenAI | model `text-embedding-3-small`, api_base `https://api.openai.com/v1`, dimension `1536` | model `gpt-5.4`, same api_base |
     | Ollama (local, no key) | model `nomic-embed-text`, api_base `http://localhost:11434/v1`, dimension `768`, input `text` | not provided — combine with another VLM provider |

     Other providers (Codex OAuth, Kimi, GLM): see `docs/en/guides/01-configuration.md`. Codex OAuth needs an interactive sign-in — hand that to the user instead of automating it.
  3. Write `~/.openviking/ov.conf`. If the file already exists, stop and ask before touching it. Template (Volcengine values shown — substitute from the table):

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

### 3. Existing state

```bash
openclaw openviking status --json
```

- `configured: true` and `slotActive: true` → already installed. Stop and report, unless the user asked for an upgrade or reconfigure.
- Command not recognized → plugin not installed; continue.

### 4. Install

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
```

If ClawHub is unreachable or rate-limited, use the backup path:

```bash
npx -y openclaw-openviking-setup-helper@latest --base-url <BASE_URL> --api-key <API_KEY>
```

The backup helper also accepts `--workdir <path>`, `--plugin-version=<REF>` (source ref), and `--update`.

### 5. Configure

```bash
openclaw openviking setup --base-url <BASE_URL> --api-key <API_KEY> --json
```

Branch on the JSON result:

| Result | Action |
| --- | --- |
| `success: true` | Continue to Step 6 |
| `action: "slot_blocked"` | Another plugin owns `contextEngine`. Ask the user; only on approval rerun with `--force-slot` |
| `action: "error"` | Report `error` and stop; do not claim success |
| `health.ok: false` | Server unreachable. Re-check Step 2; rerun with `--allow-offline` only if the user approves saving an unverified config |
| `keyProbe.keyType: "root_key"` | Ask for account/user IDs, rerun with `--account-id <ID> --user-id <ID>` |
| `health.compatibility: "server_too_old"` / `"server_too_new"` | Warn the user and recommend upgrading the server / plugin |

If the `openclaw` CLI cannot run at all (e.g. inside a container), merge this into the config file OpenClaw reads (`$OPENCLAW_CONFIG_PATH` if set, else `~/.openclaw/openclaw.json`) after backing it up, then restart the gateway/container:

```json
{
  "plugins": {
    "entries": {
      "openviking": {
        "enabled": true,
        "config": { "mode": "remote", "baseUrl": "<BASE_URL>", "apiKey": "<API_KEY>" }
      }
    },
    "slots": { "contextEngine": "openviking" }
  }
}
```

From a container, `baseUrl` must be reachable from inside the container — not `127.0.0.1` of the host.

### 6. Restart and verify

```bash
openclaw gateway restart
openclaw openviking status --json
```

Check the Success Criteria fields. Then report to the user: what was installed, which server it points at, and that new sessions now use OpenViking as the context engine.

## TODO

Copy this list into your own todo tracking and check items off as you go:

- [ ] Collect base URL and API key from the user via option prompt
- [ ] Preflight passed: Node >= 22, OpenClaw >= 2026.5.27
- [ ] Server `/health` reachable (local server set up first if the user chose that)
- [ ] Plugin installed from ClawHub (or backup helper)
- [ ] `setup --json` returned `success: true`
- [ ] Gateway restarted
- [ ] `status --json` shows `configured: true` and `slotActive: true`
- [ ] Result reported to the user

## Other Operations

Run these only on explicit user request:

| Intent | Commands |
| --- | --- |
| Upgrade | `openclaw plugins update openviking && openclaw gateway restart && openclaw openviking status --json` |
| Reconfigure | Step 5 with new values, then Step 6 |
| Uninstall | `openclaw plugins uninstall openviking && openclaw config set plugins.slots.contextEngine legacy && openclaw gateway restart` — native uninstall does not reset the slot |
| Migrate from ov-install (plugin id `openviking`, >= 0.3.x) | `rm -rf ~/.openclaw/extensions/openviking/`, then Steps 4–6 (existing config values are preserved) |
| Migrate from old `memory-openviking` (< 0.3.x) | `openclaw plugins uninstall memory-openviking; openclaw config set plugins.slots.memory none; rm -rf ~/.openclaw/extensions/memory-openviking/`, then Steps 4–6 |
| End-to-end pipeline check | `python examples/openclaw-plugin/health_check_tools/ov-healthcheck.py` (repo checkout only) |

## EXECUTE NOW

Work through Steps 1–6. Stop when the Success Criteria are met and report status. Do not start chat sessions, run the pipeline health check, or modify other plugins unless the user asks.
