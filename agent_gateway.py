"""HTTP compatibility gateway for the external dispatch-agent service."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AgentGatewayError(RuntimeError):
    """Raised when the configured agent service cannot complete a request."""


class AgentGateway:
    def __init__(self, base_url: str, token: str = "", timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            if clean_query:
                url = f"{url}?{urlencode(clean_query)}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.token:
            headers["X-Agent-Token"] = self.token
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AgentGatewayError(f"Agent service request failed: {method} {path}") from exc

    def heartbeat(self, agent_id: str, level: str) -> None:
        self._request("POST", "/v1/agents/heartbeat", {"agent_id": agent_id, "level": level})

    def online_agents(self, max_age_seconds: int = 30) -> set[str]:
        result = self._request(
            "GET", "/v1/agents/online", query={"max_age_seconds": max_age_seconds}
        )
        return set(result.get("agents", []))

    def create_ticket(self, payload: dict[str, Any]) -> str:
        result = self._request("POST", "/v1/tickets", payload)
        return str(result["ticket_no"])

    def list_tickets(
        self, receiver: str | None = None, sender_or_receiver: str | None = None
    ) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            "/v1/tickets",
            query={"receiver": receiver, "party": sender_or_receiver},
        )
        return list(result.get("tickets", []))

    def acknowledge(self, message_id: str) -> None:
        self._request("POST", f"/v1/tickets/{message_id}/ack")

    def execute(self, message_id: str, executed_by: str) -> None:
        self._request(
            "POST", f"/v1/tickets/{message_id}/execute", {"executed_by": executed_by}
        )

    def forward(self, message_id: str) -> None:
        self._request("POST", f"/v1/tickets/{message_id}/forward")
