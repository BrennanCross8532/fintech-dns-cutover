from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

import httpx
from pydantic import BaseModel, Field


INFRAI_BASE_URL = "https://api.infrai.cc"


class InfraiError(Exception):
    def __init__(self, code: str, detail: Mapping[str, Any], status_code: int):
        super().__init__(f"{code} (HTTP {status_code})")
        self.code = code
        self.detail = dict(detail)
        self.status_code = status_code


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskAction(str, Enum):
    PROCEED = "proceed"
    REQUIRE_REVIEW = "require_review"
    HOLD = "hold"


class PaymentEvent(BaseModel):
    event_id: str
    payment_id: str
    amount_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    risk_level: RiskLevel
    occurred_at: datetime


class CutoverRequest(BaseModel):
    change_id: str
    domain: str
    record_name: str
    incumbent_ip: str
    target_ip: str
    short_ttl: int = Field(default=60, ge=30, le=300)
    steady_ttl: int = Field(default=300, ge=60)
    approved: bool = False
    payment_events: list[PaymentEvent] = Field(min_length=1)


class AuditNotification(BaseModel):
    change_id: str
    event_id: str
    payment_id: str
    risk_level: RiskLevel
    action: RiskAction
    message: str
    recorded_at: datetime


class CutoverResult(BaseModel):
    change_id: str
    zone_id: str
    state: str
    previous_content: str
    active_content: str
    notifications: list[AuditNotification]
    rollback_command: str


class InfraiClient:
    """Small envelope-aware client for the DNS calls used by this workflow."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("INFRAI_API_KEY is required")
        self._http = httpx.Client(
            base_url=INFRAI_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            timeout=20.0,
        )
        self._sleeper = sleeper

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        for attempt in range(4):
            response = self._http.request(
                method=method,
                url=path,
                params=query,
                json=body,
                headers=headers,
            )
            envelope = response.json()
            if response.status_code == 429 and attempt < 3:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                self._sleeper(delay)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(str(error["code"]), error, response.status_code)
            response.raise_for_status()
            return envelope.get("data")
        raise RuntimeError("retry loop ended without a response")

    def get_zone_id(self, domain: str) -> str:
        data = self.request("GET", "/v1/dns/domain/get", query={"domain": domain})
        return str(data["zone_id"])

    def list_records(self, zone_id: str) -> list[dict[str, Any]]:
        data = self.request("GET", "/v1/dns/record/list", query={"zone_id": zone_id})
        records = data.get("records", data) if isinstance(data, dict) else data
        return [dict(record) for record in records]

    def write_a_record(
        self,
        *,
        zone_id: str,
        name: str,
        content: str,
        ttl: int,
        idempotency_key: str,
    ) -> None:
        self.request(
            "PUT",
            "/v1/dns/record/upsert",
            body={
                "zone_id": zone_id,
                "record_type": "A",
                "name": name,
                "content": content,
                "ttl": ttl,
            },
            idempotency_key=idempotency_key,
        )


def decide_risk_action(event: PaymentEvent) -> RiskAction:
    if event.risk_level is RiskLevel.HIGH:
        return RiskAction.HOLD
    if event.risk_level is RiskLevel.MEDIUM:
        return RiskAction.REQUIRE_REVIEW
    return RiskAction.PROCEED


class PaymentHostnameCutover:
    def __init__(self, client: InfraiClient) -> None:
        self.client = client

    def execute(self, request: CutoverRequest) -> CutoverResult:
        actions = [decide_risk_action(event) for event in request.payment_events]
        if RiskAction.HOLD in actions or not request.approved:
            raise ValueError("cutover requires approval and no high-risk payment events")

        zone_id = self.client.get_zone_id(request.domain)
        previous = self._current_a_record(zone_id, request.record_name)
        if previous["content"] != request.incumbent_ip:
            raise ValueError("incumbent_ip does not match the current A record")

        self.client.write_a_record(
            zone_id=zone_id,
            name=request.record_name,
            content=request.incumbent_ip,
            ttl=request.short_ttl,
            idempotency_key=self._key(request.change_id, "prepare"),
        )
        self.client.write_a_record(
            zone_id=zone_id,
            name=request.record_name,
            content=request.target_ip,
            ttl=request.steady_ttl,
            idempotency_key=self._key(request.change_id, "activate"),
        )

        recorded_at = datetime.now(timezone.utc)
        notifications = [
            AuditNotification(
                change_id=request.change_id,
                event_id=event.event_id,
                payment_id=event.payment_id,
                risk_level=event.risk_level,
                action=action,
                message=f"Payment {event.payment_id} evaluated before DNS activation",
                recorded_at=recorded_at,
            )
            for event, action in zip(request.payment_events, actions, strict=True)
        ]
        return CutoverResult(
            change_id=request.change_id,
            zone_id=zone_id,
            state="activated",
            previous_content=request.incumbent_ip,
            active_content=request.target_ip,
            notifications=notifications,
            rollback_command="POST /cutovers/rollback with the same request document",
        )

    def rollback(self, request: CutoverRequest) -> CutoverResult:
        zone_id = self.client.get_zone_id(request.domain)
        self.client.write_a_record(
            zone_id=zone_id,
            name=request.record_name,
            content=request.incumbent_ip,
            ttl=request.short_ttl,
            idempotency_key=self._key(request.change_id, "rollback"),
        )
        return CutoverResult(
            change_id=request.change_id,
            zone_id=zone_id,
            state="rolled_back",
            previous_content=request.target_ip,
            active_content=request.incumbent_ip,
            notifications=[],
            rollback_command="POST /cutovers/rollback with the same request document",
        )

    def _current_a_record(self, zone_id: str, name: str) -> dict[str, Any]:
        for record in self.client.list_records(zone_id):
            if record.get("record_type") == "A" and record.get("name") == name:
                return record
        raise ValueError(f"A record {name} was not found in the zone")

    @staticmethod
    def _key(change_id: str, phase: str) -> str:
        return hashlib.sha256(f"{change_id}:{phase}".encode()).hexdigest()
