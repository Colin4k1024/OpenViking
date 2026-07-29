import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

async function withMockOpenViking(handler, fn) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

function runSessionStart(input, env) {
  return new Promise((resolve, reject) => {
    const cleanEnv = { ...process.env };
    for (const key of Object.keys(cleanEnv)) {
      if (key.startsWith("OPENVIKING_")) delete cleanEnv[key];
    }
    const child = spawn(
      process.execPath,
      [join(SCRIPT_DIR, "session-start-commit.mjs")],
      {
        env: { ...cleanEnv, ...env },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`session-start-commit exited ${code}: ${stderr}`));
        return;
      }
      resolve();
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("session start clears a stale state after commit returns 404", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-state-"));
  const staleSessionId = "stale-session";
  const staleStatePath = join(stateDir, `${staleSessionId}.json`);
  let commitCalls = 0;

  try {
    await writeFile(
      staleStatePath,
      JSON.stringify({
        codexSessionId: staleSessionId,
        ovSessionId: `cx-${staleSessionId}`,
        lastUpdatedAt: Date.now() - 3_600_000,
      }),
    );

    await withMockOpenViking((req, res) => {
      if (req.method === "GET" && req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", result: { ok: true } }));
        return;
      }
      if (
        req.method === "POST" &&
        req.url === `/api/v1/sessions/cx-${staleSessionId}/commit`
      ) {
        commitCalls += 1;
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            status: "error",
            error: { code: "NOT_FOUND", message: "Session not found" },
          }),
        );
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error" }));
    }, async (baseUrl) => {
      const env = {
        OPENVIKING_CODEX_IDLE_TTL_MS: "1000",
        OPENVIKING_CODEX_STATE_DIR: stateDir,
        OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
        OPENVIKING_URL: baseUrl,
        OPENVIKING_WORKSPACE_PEER: "0",
      };

      await runSessionStart(
        { session_id: "current-session-1", source: "startup" },
        env,
      );
      await runSessionStart(
        { session_id: "current-session-2", source: "startup" },
        env,
      );
    });

    assert.equal(
      commitCalls,
      1,
      "a terminal 404 must not be retried on the next session start",
    );
    await assert.rejects(access(staleStatePath));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("session start keeps a stale state after a retryable commit failure", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-retry-"));
  const staleSessionId = "retry-session";
  const staleStatePath = join(stateDir, `${staleSessionId}.json`);
  let commitCalls = 0;

  try {
    await writeFile(
      staleStatePath,
      JSON.stringify({
        codexSessionId: staleSessionId,
        ovSessionId: `cx-${staleSessionId}`,
        lastUpdatedAt: Date.now() - 3_600_000,
      }),
    );

    await withMockOpenViking((req, res) => {
      if (req.method === "GET" && req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", result: { ok: true } }));
        return;
      }
      if (
        req.method === "POST" &&
        req.url === `/api/v1/sessions/cx-${staleSessionId}/commit`
      ) {
        commitCalls += 1;
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            status: "error",
            error: { code: "UNAVAILABLE", message: "Try again later" },
          }),
        );
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error" }));
    }, async (baseUrl) => {
      const env = {
        OPENVIKING_CODEX_IDLE_TTL_MS: "1000",
        OPENVIKING_CODEX_STATE_DIR: stateDir,
        OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
        OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
        OPENVIKING_CREDENTIAL_SOURCE: "env",
        OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
        OPENVIKING_URL: baseUrl,
        OPENVIKING_WORKSPACE_PEER: "0",
      };

      await runSessionStart(
        { session_id: "current-session-1", source: "startup" },
        env,
      );
      await runSessionStart(
        { session_id: "current-session-2", source: "startup" },
        env,
      );
    });

    assert.equal(commitCalls, 2, "a retryable failure must be attempted again");
    await assert.doesNotReject(access(staleStatePath));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});

test("session start commits stale state with its original workspace peer", async () => {
  const stateDir = await mkdtemp(join(tmpdir(), "ov-session-start-peer-"));
  const staleSessionId = "peer-session";
  const staleStatePath = join(stateDir, `${staleSessionId}.json`);
  let commitPeer;

  try {
    await writeFile(
      staleStatePath,
      JSON.stringify({
        codexSessionId: staleSessionId,
        ovSessionId: `cx-${staleSessionId}`,
        workspacePeerId: "original-workspace-peer",
        lastUpdatedAt: Date.now() - 3_600_000,
      }),
    );

    await withMockOpenViking((req, res) => {
      if (req.method === "GET" && req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", result: { ok: true } }));
        return;
      }
      if (
        req.method === "POST" &&
        req.url === `/api/v1/sessions/cx-${staleSessionId}/commit`
      ) {
        commitPeer = req.headers["x-openviking-actor-peer"];
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({
            status: "ok",
            result: { archived: true, task_id: "task-peer" },
          }),
        );
        return;
      }
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "error" }));
    }, async (baseUrl) => {
      await runSessionStart(
        { session_id: "current-session", source: "startup" },
        {
          OPENVIKING_CODEX_IDLE_TTL_MS: "1000",
          OPENVIKING_CODEX_STATE_DIR: stateDir,
          OPENVIKING_CONFIG_FILE: join(stateDir, "missing-ov.conf"),
          OPENVIKING_CLI_CONFIG_FILE: join(stateDir, "missing-ovcli.conf"),
          OPENVIKING_CREDENTIAL_SOURCE: "env",
          OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP: "0",
          OPENVIKING_URL: baseUrl,
        },
      );
    });

    assert.equal(commitPeer, "original-workspace-peer");
    await assert.rejects(access(staleStatePath));
  } finally {
    await rm(stateDir, { recursive: true, force: true });
  }
});
