from __future__ import annotations

import asyncio
import logging

import pytest

from orchlink.loop.adapters.connectors import ConnectorConfig, ConnectorSecretGateway, GitHubConnector
from orchlink.loop.adapters.connectors import github as github_module
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.services import ItemCandidate, LoopService


class StaticSecrets:
    def __init__(self, token: str | None) -> None:
        self.token = token

    def get(self, name: str) -> str | None:
        assert name == "github"
        return self.token


class FakeGitHubHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, url, headers, params):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "params": dict(params)})
        for marker, response in self.responses:
            if marker in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return {"status": 200, "json": []}


def run(connector: GitHubConnector):
    return asyncio.run(connector.discover())


def config(**kwargs) -> ConnectorConfig:
    return ConnectorConfig(name="github", repo=kwargs.pop("repo", "owner/repo"), **kwargs)


def test_connector_config_rejects_secret_unknown_key():
    with pytest.raises(ValueError):
        ConnectorConfig.from_dict({"name": "github", "token": "x"})


def test_connector_config_rejects_secret_unknown_key_case_insensitive():
    with pytest.raises(ValueError):
        ConnectorConfig.from_dict({"name": "github", "TOKEN": "x"})


def test_connector_config_rejects_secret_extra_key():
    with pytest.raises(ValueError):
        ConnectorConfig.from_dict({"name": "github", "extra": {"api_key": "x"}})


def test_connector_config_allows_non_secret_extra_key():
    parsed = ConnectorConfig.from_dict({"name": "github", "limit": 5, "extra": {"page_size": 10}})

    assert parsed.limit == 5
    assert parsed.extra == {"page_size": 10}


def test_connector_config_init_rejects_secret_extra_key():
    with pytest.raises(ValueError):
        ConnectorConfig(name="github", extra={"api_key": "x"})


def test_github_discover_pr_candidates():
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 200, "json": [{"number": 7, "title": "Add API", "html_url": "https://github.test/pull/7"}]}),
            ("/issues", {"status": 200, "json": []}),
            ("/commits/main/status", {"status": 200, "json": {"state": "success", "sha": "abc"}}),
        ]
    )
    connector = GitHubConnector(config(), StaticSecrets("token"), http)

    candidates = run(connector)

    assert len(candidates) == 1
    assert candidates[0].id == "pr-7"
    assert candidates[0].source_type == "github"
    assert candidates[0].source_ref == "https://github.test/pull/7"
    assert candidates[0].title == "Add API"
    assert candidates[0].objective == "Review or address pull request #7: Add API"


def test_github_discover_issue_candidates_when_prs_empty():
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 200, "json": []}),
            (
                "/issues",
                {
                    "status": 200,
                    "json": [
                        {"number": 3, "title": "Fix crash", "html_url": "https://github.test/issues/3", "labels": [{"name": "Bug"}]},
                        {"number": 4, "title": "Question", "html_url": "https://github.test/issues/4", "labels": [{"name": "question"}]},
                    ],
                },
            ),
            ("/commits/main/status", {"status": 200, "json": {"state": "success", "sha": "abc"}}),
        ]
    )

    candidates = run(GitHubConnector(config(), StaticSecrets("token"), http))

    assert [candidate.id for candidate in candidates] == ["issue-3"]
    assert candidates[0].objective == "Address GitHub issue #3: Fix crash"


def test_github_discover_ci_failure_candidate():
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 200, "json": []}),
            ("/issues", {"status": 200, "json": []}),
            ("/commits/main/status", {"status": 200, "json": {"state": "failure", "sha": "abcdef1234567890"}}),
        ]
    )

    candidates = run(GitHubConnector(config(), StaticSecrets("token"), http))

    assert len(candidates) == 1
    assert candidates[0].id == "ci-failure-abcdef123456"
    assert candidates[0].objective == "Address failing CI checks on abcdef123456"


def test_github_api_failure_returns_empty():
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 500, "json": {}}),
            ("/issues", {"status": 500, "json": {}}),
            ("/commits/main/status", {"status": 500, "json": {}}),
        ]
    )

    assert run(GitHubConnector(config(), StaticSecrets("token"), http)) == []


def test_github_malformed_json_returns_empty():
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 200, "body": "not json"}),
            ("/issues", {"status": 200, "body": "not json"}),
            ("/commits/main/status", {"status": 200, "body": "not json"}),
        ]
    )

    assert run(GitHubConnector(config(), StaticSecrets("token"), http)) == []


def test_github_missing_token_returns_empty_without_http_call():
    http = FakeGitHubHttp([])

    assert run(GitHubConnector(config(), StaticSecrets(None), http)) == []
    assert http.calls == []


def test_github_limit_caps_total_candidates():
    http = FakeGitHubHttp(
        [
            (
                "/pulls",
                {
                    "status": 200,
                    "json": [
                        {"number": 1, "title": "One", "html_url": "https://github.test/pull/1"},
                        {"number": 2, "title": "Two", "html_url": "https://github.test/pull/2"},
                        {"number": 3, "title": "Three", "html_url": "https://github.test/pull/3"},
                    ],
                },
            ),
        ]
    )

    candidates = run(GitHubConnector(config(limit=2), StaticSecrets("token"), http))

    assert [candidate.id for candidate in candidates] == ["pr-1", "pr-2"]


def test_github_missing_repo_returns_empty_without_http_call():
    http = FakeGitHubHttp([])

    assert run(GitHubConnector(config(repo=None), StaticSecrets("token"), http)) == []
    assert http.calls == []


def test_github_injects_authorization_header_and_never_logs_token(caplog):
    token = "header-secret-token"
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 500, "body": token}),
            ("/issues", {"status": 500, "body": token}),
            ("/commits/main/status", {"status": 500, "body": token}),
        ]
    )
    caplog.set_level(logging.DEBUG, logger=github_module.__name__)

    assert run(GitHubConnector(config(), StaticSecrets(token), http)) == []

    assert http.calls
    assert all(call["headers"].get("Authorization") == f"Bearer {token}" for call in http.calls)
    assert all(token not in record.getMessage() for record in caplog.records)


def test_github_http_exception_does_not_log_token_or_authorization_header(caplog):
    token = "exception-secret-token"

    def raising_http(method, url, headers, params):
        raise RuntimeError(f"boom {token} Authorization: Bearer {token}")

    caplog.set_level(logging.DEBUG, logger=github_module.__name__)

    assert run(GitHubConnector(config(), StaticSecrets(token), raising_http)) == []

    messages = [record.getMessage() for record in caplog.records]
    assert messages
    assert all(token not in message for message in messages)
    assert all("Bearer " not in message for message in messages)
    assert all("Authorization" not in message for message in messages)


def test_github_uses_discovered_default_branch_for_ci_status():
    calls = []

    def http(method, url, headers, params):
        calls.append(url)
        if url.endswith("/repos/owner/repo"):
            return {"status": 200, "json": {"default_branch": "develop"}}
        if url.endswith("/pulls") or url.endswith("/issues"):
            return {"status": 200, "json": []}
        if url.endswith("/commits/develop/status"):
            return {"status": 200, "json": {"state": "failure", "sha": "deadcafe123456"}}
        raise AssertionError(url)

    candidates = run(GitHubConnector(config(), StaticSecrets("token"), http))

    assert [candidate.id for candidate in candidates] == ["ci-failure-deadcafe1234"]
    assert any(url.endswith("/commits/develop/status") for url in calls)


def test_github_default_branch_metadata_failure_falls_back_to_main():
    calls = []

    def http(method, url, headers, params):
        calls.append(url)
        if url.endswith("/repos/owner/repo"):
            return {"status": 500, "json": {}}
        if url.endswith("/pulls") or url.endswith("/issues"):
            return {"status": 200, "json": []}
        if url.endswith("/commits/main/status"):
            return {"status": 200, "json": {"state": "success", "sha": "abc"}}
        raise AssertionError(url)

    assert run(GitHubConnector(config(), StaticSecrets("token"), http)) == []
    assert any(url.endswith("/commits/main/status") for url in calls)


def test_github_discover_does_not_serialize_token_to_loop_state(tmp_path):
    token = "state-secret-token"
    http = FakeGitHubHttp(
        [
            ("/pulls", {"status": 200, "json": [{"number": 7, "title": "Add API", "html_url": "https://github.test/pull/7"}]}),
            ("/issues", {"status": 200, "json": []}),
            ("/commits/main/status", {"status": 200, "json": {"state": "success", "sha": "abc"}}),
        ]
    )
    candidates = run(GitHubConnector(config(), StaticSecrets(token), http))
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
    assert "https://github.test/pull/7" in content
