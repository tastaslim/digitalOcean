import pytest

from app.resources.proxy.proxyService import _extractAction, _getContent


# ---------------------------------------------------------------------------
# _extractAction
# ---------------------------------------------------------------------------


def test_extractAction_returns_action_value() -> None:
    resp = {"choices": [{"message": {"content": '{"action": "buy"}'}}]}
    assert _extractAction(resp) == "buy"


def test_extractAction_returns_none_for_invalid_json() -> None:
    resp = {"choices": [{"message": {"content": "not json"}}]}
    assert _extractAction(resp) is None


def test_extractAction_returns_none_when_action_key_missing() -> None:
    resp = {"choices": [{"message": {"content": '{"step": "review"}'}}]}
    assert _extractAction(resp) is None


def test_extractAction_returns_none_for_missing_choices() -> None:
    assert _extractAction({}) is None


def test_extractAction_returns_none_for_empty_choices() -> None:
    assert _extractAction({"choices": []}) is None


def test_extractAction_returns_none_for_missing_content() -> None:
    resp = {"choices": [{"message": {}}]}
    assert _extractAction(resp) is None


def test_extractAction_returns_none_for_non_string_content() -> None:
    resp = {"choices": [{"message": {"content": 42}}]}
    assert _extractAction(resp) is None


def test_extractAction_handles_nested_action() -> None:
    resp = {"choices": [{"message": {"content": '{"action": "sell", "qty": 10}'}}]}
    assert _extractAction(resp) == "sell"


# ---------------------------------------------------------------------------
# _getContent
# ---------------------------------------------------------------------------


def test_getContent_returns_content_string() -> None:
    resp = {"choices": [{"message": {"content": "hello"}}]}
    assert _getContent(resp) == "hello"


def test_getContent_returns_empty_on_missing_key() -> None:
    assert _getContent({}) == ""


def test_getContent_returns_empty_on_empty_choices() -> None:
    assert _getContent({"choices": []}) == ""
