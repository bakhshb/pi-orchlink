from __future__ import annotations

import asyncio
import logging

from orchlink.loop.adapters.connectors import ConnectorConfig, LinearConnector
from orchlink.loop.adapters.connectors import linear as linear_module
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.services import ItemCandidate, LoopService


class StaticSecrets:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def get(self, name: str) -> str | None:
        assert name == "linear"
        return self.token


class FakeLinearHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, headers, params):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "params": dict(params)})
        key = "recent" if params.get("recent") else "issues"
        response = self.responses.get(key, self.responses.get("*", {"status": 200, "json": {"data": {"issues": {"nodes": []}}}}))
        if isinstance(response, Exception):
            raise response
        return response


def run(connector: LinearConnector):
    return asyncio.run(connector.discover())


def config(**kwargs) -> ConnectorConfig:
    extra = kwargs.pop("extra", {"team": "ENG"})
    return ConnectorConfig(name="linear", repo=kwargs.pop("repo", None), extra=extra, **kwargs)


def issue(identifier="ENG-123", title="Fix bug", url="https://linear.test/ENG-123"):
    return {"identifier": identifier, "title": title, "url": url}


def test_linear_discover_issue_candidates():
    http = FakeLinearHttp({"issues": {"status": 200, "json": {"data": {"issues": {"nodes": [issue()]}}}}})

    candidates = run(LinearConnector(config(), StaticSecrets("token"), http))

    assert len(candidates) == 2
    assert candidates[0].id == "issue-ENG-123"
    assert candidates[0].source_type == "linear"
    assert candidates[0].source_ref == "https://linear.test/ENG-123"
    assert candidates[0].title == "Fix bug"
    assert candidates[0].objective == "Address Linear issue ENG-123: Fix bug"


def test_linear_empty_issues_emits_recent_activity_candidate():
    http = FakeLinearHttp(
        {
            "issues": {"status": 200, "json": {"data": {"issues": {"nodes": []}}}},
            "recent": {"status": 200, "json": {"data": {"issues": {"nodes": []}}}},
        }
    )

    candidates = run(LinearConnector(config(), StaticSecrets("token"), http))

    assert [candidate.id for candidate in candidates] == ["linear-recent"]
    assert candidates[0].objective == "Review recent Linear activity for the configured team."


def test_linear_missing_token_returns_empty_without_http_call():
    http = FakeLinearHttp({})

    assert run(LinearConnector(config(), StaticSecrets(None), http)) == []
    assert http.calls == []


def test_linear_api_failure_returns_empty():
    http = FakeLinearHttp({"issues": {"status": 500, "json": {}}, "recent": {"status": 500, "json": {}}})

    assert run(LinearConnector(config(), StaticSecrets("token"), http)) == []


def test_linear_malformed_json_returns_empty():
    http = FakeLinearHttp({"issues": {"status": 200, "body": "not json"}, "recent": {"status": 200, "body": "not json"}})

    assert run(LinearConnector(config(), StaticSecrets("token"), http)) == []


def test_linear_limit_caps_total_candidates():
    http = FakeLinearHttp(
        {
            "issues": {
                "status": 200,
                "json": {
                    "data": {
                        "issues": {
                            "nodes": [
                                issue("ENG-1", "One", "https://linear.test/ENG-1"),
                                issue("ENG-2", "Two", "https://linear.test/ENG-2"),
                                issue("ENG-3", "Three", "https://linear.test/ENG-3"),
                            ]
                        }
                    }
                },
            }
        }
    )

    candidates = run(LinearConnector(config(limit=2), StaticSecrets("token"), http))

    assert [candidate.id for candidate in candidates] == ["issue-ENG-1", "issue-ENG-2"]


def test_linear_missing_team_or_project_returns_empty_without_http_call():
    http = FakeLinearHttp({})

    assert run(LinearConnector(config(extra={}, repo=None), StaticSecrets("token"), http)) == []
    assert http.calls == []


def test_linear_injects_authorization_header_and_never_logs_token(caplog):
    token = "linear-secret-token"
    http = FakeLinearHttp({"issues": {"status": 500, "body": token}, "recent": {"status": 500, "body": token}})
    caplog.set_level(logging.DEBUG, logger=linear_module.__name__)

    assert run(LinearConnector(config(), StaticSecrets(token), http)) == []

    assert http.calls
    assert all(call["headers"].get("Authorization") == f"Bearer {token}" for call in http.calls)
    assert all(token not in record.getMessage() for record in caplog.records)


def test_linear_http_exception_does_not_log_token_or_authorization_header(caplog):
    token = "exception-linear-token"

    def raising_http(method, url, headers, params):
        raise RuntimeError(f"boom {token} Authorization: Bearer {token}")

    caplog.set_level(logging.DEBUG, logger=linear_module.__name__)

    assert run(LinearConnector(config(), StaticSecrets(token), raising_http)) == []

    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert all(token not in message for message in messages)
    assert all("Bearer " not in message for message in messages)
    assert all("Authorization" not in message for message in messages)


def test_linear_discover_does_not_serialize_token_to_loop_state(tmp_path):
    token = "state-linear-token"
    http = FakeLinearHttp({"issues": {"status": 200, "json": {"data": {"issues": {"nodes": [issue()]}}}}})
    candidates = run(LinearConnector(config(), StaticSecrets(token), http))
    repo = LoopStateRepo(tmp_path)
    service = LoopService({}, repo)
    service.triage(
        [
            ItemCandidate(
                item_id=candidate.id,
                title=candidate.title,
                source_type=candidate.source_type,
                source_ref=candidate.source_ref,
            )
            for candidate in candidates
        ]
    )

    content = repo.state_path.read_text(encoding="utf-8")

    assert token not in content
    assert "https://linear.test/ENG-123" in content
