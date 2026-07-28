"""Fail-closed checks for content-only tool-call promotion."""

from scripts.opd.response_router import route_content_tool_call


def test_strict_router_promotes_one_closed_schema_valid_call() -> None:
    decision = route_content_tool_call(
        "<tool_call><function=navigate><parameter=x>10</parameter>"
        "<parameter=y>20</parameter></function></tool_call>"
    )
    assert decision == {
        "status": "promoted",
        "calls": [{"name": "navigate", "args": {"x": 10, "y": 20}}],
        "reason": "valid",
    }


def test_strict_router_quarantines_corrupt_outer_envelope() -> None:
    decision = route_content_tool_call(
        "<tool_call><function=navigate><parameter=x>10</parameter>"
        "<parameter=y>20</parameter></function>"
    )
    assert decision["status"] == "quarantined"
    assert decision["reason"] == "invalid_tool_call_envelope"
    assert decision["calls"] == []


def test_strict_router_quarantines_schema_invalid_candidate() -> None:
    decision = route_content_tool_call(
        "<tool_call><function=gather>{}</function></tool_call>"
    )
    assert decision["status"] == "quarantined"
    assert decision["reason"] == "missing_required_argument"
    assert decision["calls"] == []


def test_strict_router_does_not_invent_a_candidate() -> None:
    assert route_content_tool_call("I should observe next.") == {
        "status": "no_candidate",
        "calls": [],
        "reason": "not_recoverable",
    }
