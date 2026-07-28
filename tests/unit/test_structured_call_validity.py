import json

from scripts.opd.analyze_structured_call_validity import validate_structured_call


def _call(name: str, arguments: dict) -> dict:
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_accepts_registered_call_with_valid_arguments() -> None:
    assert validate_structured_call(_call("observe", {})) == (True, "valid")


def test_rejects_unknown_function_and_missing_argument() -> None:
    assert validate_structured_call(_call("observe_now", {}))[1] == "unknown_function"
    assert validate_structured_call(_call("attack", {}))[1] == (
        "missing_required_argument"
    )


def test_rejects_wrong_type_and_unknown_argument() -> None:
    assert validate_structured_call(_call("eat_food", {"slot": "1"}))[1] == (
        "wrong_argument_type"
    )
    assert validate_structured_call(_call("observe", {"nonce": 1}))[1] == (
        "unknown_argument"
    )
