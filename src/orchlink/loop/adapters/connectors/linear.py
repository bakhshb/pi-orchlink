"""Read-only Linear loop triage connector."""

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


class LinearConnector:
    name = "linear"

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
            log.debug("linear connector discover failed: type=%s", type(exc).__name__)
            return []

    def _discover_sync(self) -> list[ItemCandidate]:
        scope = self._scope_filter()
        if scope is None:
            log.debug("linear connector missing team/project config")
            return []
        token = self.secrets.get("linear")
        if not token:
            log.debug("linear connector token missing")
            return []
        limit = max(0, int(self.config.limit))
        if limit <= 0:
            return []
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "orchlink-loop-connector",
        }

        candidates = self._issue_candidates(headers, scope, limit)
        if len(candidates) >= limit:
            return candidates[:limit]
        recent = self._recent_activity_candidate(headers, scope, min(limit, 5))
        if recent is not None:
            candidates.append(recent)
        return candidates[:limit]

    def _issue_candidates(self, headers: dict[str, str], scope: dict[str, Any], limit: int) -> list[ItemCandidate]:
        body = self._query_issues(headers, scope, limit=limit, recent=False)
        issues = self._issue_nodes(body)
        if issues is None:
            return []
        candidates: list[ItemCandidate] = []
        for issue in issues:
            if len(candidates) >= limit:
                break
            candidate = self._issue_candidate(issue)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _recent_activity_candidate(self, headers: dict[str, str], scope: dict[str, Any], limit: int) -> ItemCandidate | None:
        body = self._query_issues(headers, scope, limit=limit, recent=True)
        if self._issue_nodes(body) is None:
            return None
        return ItemCandidate(
            id="linear-recent",
            source_type="linear",
            source_ref=self._scope_ref(scope),
            title="Recent Linear activity",
            objective="Review recent Linear activity for the configured team.",
            priority=Priority.NORMAL,
            suggested_skill=None,
            suggested_worktree=None,
            source_url=self._scope_ref(scope),
            source_context="",
            source_metadata={"kind": "recent_activity", **scope},
        )

    def _query_issues(self, headers: dict[str, str], scope: dict[str, Any], *, limit: int, recent: bool) -> Any:
        query = "query OrchlinkLinearIssues($first: Int!, $filter: IssueFilter) { issues(first: $first, filter: $filter) { nodes { identifier title url description updatedAt } } }"
        variables: dict[str, Any] = {"first": limit, "filter": self._linear_filter(scope)}
        if recent:
            variables["orderBy"] = "updatedAt"
        return self._get_json({"query": query, "variables": variables, "recent": recent, "headers": headers})

    def _get_json(self, params: dict[str, Any]) -> Any:
        url = (self.config.api_base or "https://api.linear.app/graphql").rstrip("/")
        headers = params.pop("headers", None) or {}
        try:
            response = self.http_client("POST", url, headers, params)
        except Exception as exc:
            log.debug(
                "linear connector request failed: type=%s url=%s",
                type(exc).__name__,
                self._safe_url(url),
            )
            return None
        status = int(response.get("status", 200))
        if status < 200 or status >= 300:
            log.debug("linear connector API failure: status=%s", status)
            return None
        if "json" in response:
            return response["json"]
        body = response.get("body", "")
        if isinstance(body, (dict, list)):
            return body
        try:
            return json.loads(str(body or ""))
        except (TypeError, ValueError) as exc:
            log.debug("linear connector JSON parse failed: type=%s", type(exc).__name__)
            return None

    def _issue_nodes(self, body: Any) -> list[dict[str, Any]] | None:
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if not isinstance(body, dict):
            return None
        if isinstance(body.get("issues"), list):
            return [item for item in body["issues"] if isinstance(item, dict)]
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        if not isinstance(data, dict):
            return None
        issues = data.get("issues")
        if isinstance(issues, list):
            return [item for item in issues if isinstance(item, dict)]
        if isinstance(issues, dict):
            nodes = issues.get("nodes")
            if isinstance(nodes, list):
                return [item for item in nodes if isinstance(item, dict)]
        return None

    def _issue_candidate(self, issue: dict[str, Any]) -> ItemCandidate | None:
        identifier = str(issue.get("identifier") or issue.get("id") or "").strip()
        title = str(issue.get("title") or "").strip()
        if not identifier or not title:
            return None
        url = str(issue.get("url") or issue.get("html_url") or identifier)
        return ItemCandidate(
            id=f"issue-{identifier}",
            source_type="linear",
            source_ref=url,
            title=title,
            objective=f"Address Linear issue {identifier}: {title}",
            priority=Priority.NORMAL,
            suggested_skill=None,
            suggested_worktree=None,
            source_url=url,
            source_context=str(issue.get("description") or issue.get("body") or ""),
            source_metadata={"kind": "issue", "identifier": identifier, "updated_at": issue.get("updatedAt")},
        )

    def _scope_filter(self) -> dict[str, str] | None:
        extra = self.config.extra
        team = extra.get("team") or extra.get("team_id") or self.config.repo
        project = extra.get("project") or extra.get("project_id")
        if team:
            return {"team": str(team)}
        if project:
            return {"project": str(project)}
        return None

    def _linear_filter(self, scope: dict[str, str]) -> dict[str, Any]:
        if "team" in scope:
            return {"team": {"key": {"eq": scope["team"]}}}
        return {"project": {"id": {"eq": scope["project"]}}}

    def _scope_ref(self, scope: dict[str, str]) -> str:
        if "team" in scope:
            return f"team:{scope['team']}"
        return f"project:{scope['project']}"

    def _safe_url(self, url: str) -> str:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.hostname or ""
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _urllib_http_client(method: str, url: str, headers: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(params).encode("utf-8") if method.upper() == "POST" else None
    request = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - connector uses configured public API URL.
            body = response.read().decode("utf-8")
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}


__all__ = ["LinearConnector"]
