from datetime import datetime, timezone

import pytest

from payment_cutover import (
    CutoverRequest,
    PaymentEvent,
    PaymentHostnameCutover,
    RiskLevel,
)


class RecordingClient:
    def __init__(self) -> None:
        self.writes: list[dict[str, object]] = []

    def get_zone_id(self, domain: str) -> str:
        assert domain == "pay.example.com"
        return "zone_fintech_42"

    def list_records(self, zone_id: str) -> list[dict[str, object]]:
        assert zone_id == "zone_fintech_42"
        return [
            {
                "record_type": "A",
                "name": "checkout",
                "content": "192.0.2.10",
                "ttl": 300,
            }
        ]

    def write_a_record(self, **values: object) -> None:
        self.writes.append(values)


def request_with(risk_level: RiskLevel) -> CutoverRequest:
    return CutoverRequest(
        change_id="chg-2026-09-18-01",
        domain="pay.example.com",
        record_name="checkout",
        incumbent_ip="192.0.2.10",
        target_ip="198.51.100.20",
        approved=True,
        payment_events=[
            PaymentEvent(
                event_id="evt-81",
                payment_id="pay-73",
                amount_minor=12500,
                currency="USD",
                risk_level=risk_level,
                occurred_at=datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc),
            )
        ],
    )


def test_low_risk_event_prepares_short_ttl_then_activates_target() -> None:
    client = RecordingClient()

    result = PaymentHostnameCutover(client).execute(request_with(RiskLevel.LOW))

    assert result.state == "activated"
    assert result.zone_id == "zone_fintech_42"
    assert result.notifications[0].action == "proceed"
    assert [(write["content"], write["ttl"]) for write in client.writes] == [
        ("192.0.2.10", 60),
        ("198.51.100.20", 300),
    ]


def test_high_risk_event_holds_cutover_before_dns_writes() -> None:
    client = RecordingClient()

    with pytest.raises(ValueError, match="no high-risk payment events"):
        PaymentHostnameCutover(client).execute(request_with(RiskLevel.HIGH))

    assert client.writes == []
