from __future__ import annotations

import logging

import pytest

from orchlink.loop.adapters.connectors import ConnectorSecretGateway, ConnectorSecretMissing
from orchlink.loop.adapters.connectors import secrets as secrets_module


def test_secret_get_prefers_env(monkeypatch, tmp_path):
    token_file = tmp_path / "github.token"
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("ORCHLINK_GITHUB_TOKEN", "env-token")

    assert ConnectorSecretGateway(tmp_path).get("github") == "env-token"


def test_secret_get_reads_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHLINK_GITHUB_TOKEN", raising=False)
    (tmp_path / "github.token").write_text("file-token\n", encoding="utf-8")

    assert ConnectorSecretGateway(tmp_path).get("github") == "file-token"


def test_secret_get_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHLINK_GITHUB_TOKEN", raising=False)

    assert ConnectorSecretGateway(tmp_path).get("github") is None


def test_secret_require_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHLINK_GITHUB_TOKEN", raising=False)

    with pytest.raises(ConnectorSecretMissing) as excinfo:
        ConnectorSecretGateway(tmp_path).require("github")

    assert excinfo.value.name == "github"


def test_secret_require_returns_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCHLINK_GITHUB_TOKEN", "env-token")

    assert ConnectorSecretGateway(tmp_path).require("github") == "env-token"


def test_secret_values_are_never_logged(monkeypatch, tmp_path, caplog):
    token = "super-secret-token"
    monkeypatch.setenv("ORCHLINK_GITHUB_TOKEN", token)
    caplog.set_level(logging.DEBUG, logger=secrets_module.__name__)

    assert ConnectorSecretGateway(tmp_path).get("github") == token

    assert caplog.records
    assert all(token not in record.getMessage() for record in caplog.records)


def test_secret_dir_under_orch_is_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("ORCHLINK_GITHUB_TOKEN", raising=False)
    unsafe = tmp_path / ".orch" / "secrets"
    unsafe.mkdir(parents=True)
    monkeypatch.setenv("ORCHLINK_SECRETS_DIR", str(unsafe))
    gateway = ConnectorSecretGateway()

    with pytest.raises(ConnectorSecretMissing) as get_exc:
        gateway.get("github")
    with pytest.raises(ConnectorSecretMissing) as require_exc:
        gateway.require("github")

    assert "outside .orch" in str(get_exc.value)
    assert "outside .orch" in str(require_exc.value)
