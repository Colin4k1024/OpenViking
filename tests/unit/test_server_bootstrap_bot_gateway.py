# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


import json

import openviking.server.bootstrap as bootstrap
from openviking_cli.utils.config.consts import OPENVIKING_CLI_CONFIG_ENV


class _FakeProcess:
    pid = 12345

    def poll(self):
        return None


def test_start_vikingbot_gateway_defaults_to_localhost_host(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["cmd"][:2] == ["vikingbot", "gateway"]
    assert "--host" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--host") + 1] == "127.0.0.1"
    assert "--port" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--port") + 1] == str(
        bootstrap.VIKINGBOT_DEFAULT_PORT
    )


def test_start_vikingbot_gateway_honors_host_argument(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        host="0.0.0.0",
    )

    assert process is not None
    host_idx = captured["cmd"].index("--host")
    assert captured["cmd"][host_idx + 1] == "0.0.0.0"


def test_read_bot_gateway_host_reads_from_config(tmp_path):
    conf_path = tmp_path / "ov.conf"
    conf_path.write_text(
        json.dumps({"bot": {"gateway": {"host": "0.0.0.0", "port": 18790}}}),
        encoding="utf-8",
    )

    host = bootstrap._read_bot_gateway_host(conf_path)
    assert host == "0.0.0.0"


def test_read_bot_gateway_host_returns_none_when_absent(tmp_path):
    conf_path = tmp_path / "ov.conf"
    conf_path.write_text("{}", encoding="utf-8")

    assert bootstrap._read_bot_gateway_host(conf_path) is None
    assert bootstrap._read_bot_gateway_host(None) is None


def test_read_bot_gateway_host_ignores_non_string_host(tmp_path):
    conf_path = tmp_path / "ov.conf"
    conf_path.write_text(
        json.dumps({"bot": {"gateway": {"host": 123}}}),
        encoding="utf-8",
    )
    assert bootstrap._read_bot_gateway_host(conf_path) is None


def test_read_bot_gateway_host_strips_whitespace_and_empty(tmp_path):
    conf_path = tmp_path / "ov.conf"
    conf_path.write_text(
        json.dumps({"bot": {"gateway": {"host": "  0.0.0.0  "}}}),
        encoding="utf-8",
    )
    assert bootstrap._read_bot_gateway_host(conf_path) == "0.0.0.0"

    # Empty string (after strip) returns None
    conf_path.write_text(
        json.dumps({"bot": {"gateway": {"host": "   "}}}),
        encoding="utf-8",
    )
    assert bootstrap._read_bot_gateway_host(conf_path) is None


def test_resolved_config_path_env_file_feeds_bot_gateway_host(monkeypatch, tmp_path):
    """Regression: config resolution through env/default path, not just explicit --config.

    ``main()`` calls ``resolve_config_path(args.config, ...)`` which honors
    ``OPENVIKING_CONFIG_FILE`` and the default ``~/.openviking/ov.conf``.
    The bot gateway host must come from that same resolved path, not just
    the explicit ``args.config`` argument.
    """
    from openviking_cli.utils.config.config_loader import resolve_config_path
    from openviking_cli.utils.config.consts import OPENVIKING_CONFIG_ENV

    env_conf = tmp_path / "from-env.conf"
    env_conf.write_text(
        json.dumps({"bot": {"gateway": {"host": "192.168.1.10"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(env_conf))

    # Simulate what main() does: resolve without explicit --config argument
    resolved = resolve_config_path(None, OPENVIKING_CONFIG_ENV, "ov.conf")
    assert resolved == env_conf

    host = bootstrap._read_bot_gateway_host(resolved)
    assert host == "192.168.1.10"


def test_start_vikingbot_gateway_uses_custom_port(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        port=19990,
    )

    assert process is not None
    # Host defaults to 127.0.0.1 when not explicitly provided
    assert captured["cmd"][captured["cmd"].index("--host") + 1] == "127.0.0.1"
    assert captured["cmd"][captured["cmd"].index("--port") + 1] == "19990"


def test_start_vikingbot_gateway_prefers_colocated_ovcli_conf(monkeypatch, tmp_path):
    captured = {}
    config_path = tmp_path / "ov.conf"
    cli_config_path = tmp_path / "ovcli.conf"
    config_path.write_text("{}", encoding="utf-8")
    cli_config_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
    monkeypatch.delenv(OPENVIKING_CLI_CONFIG_ENV, raising=False)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        config_path=str(config_path),
    )

    assert process is not None
    assert captured["env"][OPENVIKING_CLI_CONFIG_ENV] == str(cli_config_path)


def test_start_vikingbot_gateway_preserves_explicit_cli_config_env(monkeypatch, tmp_path):
    captured = {}
    config_path = tmp_path / "ov.conf"
    colocated_cli_config = tmp_path / "ovcli.conf"
    explicit_cli_config = tmp_path / "custom-ovcli.conf"
    config_path.write_text("{}", encoding="utf-8")
    colocated_cli_config.write_text("{}", encoding="utf-8")
    explicit_cli_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
    monkeypatch.setenv(OPENVIKING_CLI_CONFIG_ENV, str(explicit_cli_config))

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        config_path=str(config_path),
    )

    assert process is not None
    assert captured["env"][OPENVIKING_CLI_CONFIG_ENV] == str(explicit_cli_config)


def test_start_vikingbot_gateway_passes_managed_server_runtime(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/vikingbot")

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["env"] = env
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(
        enable_logging=False,
        log_dir="/tmp/logs",
        managed_server_url="http://127.0.0.1:1940",
    )

    assert process is not None
    assert captured["env"]["VIKINGBOT_WITH_OPENVIKING_SERVER"] == "1"
    assert captured["env"]["VIKINGBOT_MANAGED_OV_SERVER_URL"] == "http://127.0.0.1:1940"


def test_start_vikingbot_gateway_allows_slow_module_probe(monkeypatch):
    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: None)

    def _fake_run(cmd, capture_output=None, timeout=None):
        captured["probe_cmd"] = cmd
        captured["probe_timeout"] = timeout
        return type("Result", (), {"returncode": 0})()

    def _fake_popen(cmd, stdout=None, stderr=None, text=None, env=None):
        captured["cmd"] = cmd
        return _FakeProcess()

    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_run)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)

    process = bootstrap._start_vikingbot_gateway(enable_logging=False, log_dir="/tmp/logs")

    assert process is not None
    assert captured["probe_cmd"][1:] == ["-m", "vikingbot", "--help"]
    assert captured["probe_timeout"] == 15
    assert captured["cmd"][1:4] == ["-m", "vikingbot", "gateway"]
