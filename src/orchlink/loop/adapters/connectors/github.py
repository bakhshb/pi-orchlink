"""Read-only GitHub loop triage connector."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from orchlink.loop.adapters.connectors.config import ConnectorConfig
from orchlink.loop.adapters.connectors.secrets import ConnectorSecretGateway
from orchlink.loop.services.triage_service import ItemCandidate, Priority

log = logging.getLogger(__name__)

HttpClient = Callable[[str, str, dict[str, str], dict[str, Any]], dict[str, Any]]
_WORK_LABELS = {"bug", "enhancement", "good first issue", "help wanted"}


def _login(value: Any) -> str | None:
    return str(value.get("login")) if isinstance(value, dict) and value.get("login") else None


def _label_names(labels: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, dict) and label.get("name"):
            names.append(str(label["name"]))
        elif isinstance(label, str):
            names.append(label)
    return names


class GitHubConnector:
    name = "github"

    def __init__(
        self,
        config: ConnectorConfig | dict[str, Any] | None = None,
        secrets: ConnectorSecretGateway | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        if isinstance(config, ConnectorConfig):
            self.config = config
        else:
            data = dict(config or {})
            if "name" not in data:
                data["name"] = self.name
            self.config = ConnectorConfig.from_dict(data)
        self.secrets = secrets or ConnectorSecretGateway()
        self.http_client = http_client or _urllib_http_client

    async def discover(self) -> list[ItemCandidate]:
        try:
            return self._discover_sync()
        except Exception as exc:
            log.debug("github connector discover failed: type=%s", type(exc).__name__)
            return []

    def _discover_sync(self) -> list[ItemCandidate]:
        repo = (self.config.repo or "").strip()
        if not repo:
            log.debug("github connector missing repo")
            return []
        token = self.secrets.get("github")
        if not token:
            log.debug("github connector token missing")
            return []
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "orchlink-loop-connector",
        }
        candidates: list[ItemCandidate] = []
        limit = max(0, int(self.config.limit))
        if limit <= 0:
            return []

        candidates.extend(self._pull_request_candidates(repo, headers, limit))
        if len(candidates) >= limit:
            return candidates[:limit]
        candidates.extend(self._issue_candidates(repo, headers, limit - len(candidates)))
        if len(candidates) >= limit:
            return candidates[:limit]
        candidates.extend(self._ci_failure_candidates(repo, headers, limit - len(candidates)))
        return candidates[:limit]

    def _pull_request_candidates(self, repo: str, headers: dict[str, str], limit: int) -> list[ItemCandidate]:
        body = self._get_json(f"/repos/{repo}/pulls", headers, {"state": "open", "per_page": str(limit)})
        if not isinstance(body, list):
            return []
        candidates: list[ItemCandidate] = []
        for pr in body[:limit]:
            if not isinstance(pr, dict):
                continue
            number = pr.get("number")
            title = str(pr.get("title") or f"Pull request #{number}")
            url = str(pr.get("html_url") or pr.get("url") or "")
            if number is None or not url:
                continue
            candidates.append(
                ItemCandidate(
                    id=f"pr-{number}",
                    source_type="github",
                    source_ref=url,
                    title=title,
                    objective=f"Review or address pull request #{number}: {title}",
                    priority=Priority.NORMAL,
                    suggested_skill=None,
                    suggested_worktree=None,
                    source_url=url,
                    source_context=str(pr.get("body") or ""),
                    source_metadata={"kind": "pull_request", "number": number, "repo": repo, "author": _login(pr.get("user"))},
                )
            )
        return candidates

    def _issue_candidates(self, repo: str, headers: dict[str, str], limit: int) -> list[ItemCandidate]:
        body = self._get_json(f"/repos/{repo}/issues", headers, {"state": "open", "per_page": str(limit)})
        if not isinstance(body, list):
            return []
        candidates: list[ItemCandidate] = []
        for issue in body:
            if len(candidates) >= limit:
                break
            if not isinstance(issue, dict) or issue.get("pull_request"):
                continue
            labels = issue.get("labels") or []
            if not self._has_work_label(labels):
                continue
            number = issue.get("number")
            title = str(issue.get("title") or f"Issue #{number}")
            url = str(issue.get("html_url") or issue.get("url") or "")
            if number is None or not url:
                continue
            candidates.append(
                ItemCandidate(
                    id=f"issue-{number}",
                    source_type="github",
                    source_ref=url,
                    title=title,
                    objective=f"Address GitHub issue #{number}: {title}",
                    priority=Priority.NORMAL,
                    suggested_skill=None,
                    suggested_worktree=None,
                    source_url=url,
                    source_context=str(issue.get("body") or ""),
                    source_metadata={"kind": "issue", "number": number, "repo": repo, "labels": _label_names(labels), "author": _login(issue.get("user"))},
                )
            )
        return candidates

    def _ci_failure_candidates(self, repo: str, headers: dict[str, str], limit: int) -> list[ItemCandidate]:
        if limit <= 0:
            return []
        branch = str(self.config.extra.get("default_branch") or self._default_branch(repo, headers))
        body = self._get_json(f"/repos/{repo}/commits/{branch}/status", headers, {})
        if not isinstance(body, dict):
            return []
        state = str(body.get("state") or "").lower()
        statuses = body.get("statuses") or []
        failing = state in {"failure", "error"} or any(
            isinstance(status, dict) and str(status.get("state") or "").lower() in {"failure", "error"}
            for status in statuses
        )
        if not failing:
            return []
        sha = str(body.get("sha") or "")[:12] or branch
        return [
            ItemCandidate(
                id=f"ci-failure-{sha}",
                source_type="github",
                source_ref=str(body.get("target_url") or f"https://github.com/{repo}/commits/{sha}"),
                title=f"Failing CI on {sha}",
                objective=f"Address failing CI checks on {sha}",
                priority=Priority.HIGH,
                suggested_skill=None,
                suggested_worktree=None,
                source_url=str(body.get("target_url") or f"https://github.com/{repo}/commits/{sha}"),
                source_context=str(body.get("description") or ""),
                source_metadata={"kind": "ci_status", "repo": repo, "branch": branch, "sha": sha, "state": state},
            )
        ]

    def _get_json(self, path: str, headers: dict[str, str], params: dict[str, Any]) -> Any:
        base = (self.config.api_base or "https://api.github.com").rstrip("/")
        url = f"{base}{path}"
        try:
            response = self.http_client("GET", url, headers, params)
        except Exception as exc:
            log.debug(
                "github connector request failed for %s: type=%s url=%s",
                path,
                type(exc).__name__,
                self._safe_url(url),
            )
            return None
        status = int(response.get("status", 200))
        if status < 200 or status >= 300:
            log.debug("github connector API failure for %s: status=%s", path, status)
            return None
        if "json" in response:
            return response["json"]
        body = response.get("body", "")
        if isinstance(body, (dict, list)):
            return body
        try:
            return json.loads(str(body or ""))
        except (TypeError, ValueError) as exc:
            log.debug("github connector JSON parse failed for %s: type=%s", path, type(exc).__name__)
            return None

    def _default_branch(self, repo: str, headers: dict[str, str]) -> str:
        body = self._get_json(f"/repos/{repo}", headers, {})
        if isinstance(body, dict) and body.get("default_branch"):
            return str(body["default_branch"])
        return "main"

    def _safe_url(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    def _has_work_label(self, labels: list[Any]) -> bool:
        for label in labels:
            if isinstance(label, dict):
                name = str(label.get("name") or "").lower()
            else:
                name = str(label).lower()
            if name in _WORK_LABELS:
                return True
        return False


def _urllib_http_client(method: str, url: str, headers: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request_url = f"{url}?{query}" if query else url
    request = urllib.request.Request(request_url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - connector uses configured public API URL.
            body = response.read().decode("utf-8")
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}


__all__ = ["GitHubConnector"]
