"""Published package versions never substitute for wire compatibility."""

import pytest

from yoreni.webui.client_contract import assess_webui_contract, compatibility_error, webui_contract


def test_current_host_declares_the_agent_webui_contract():
    contract = webui_contract()
    assert contract["min_protocol"] == 2
    assert contract["max_protocol"] == 2
    assert "webui.core.v2" in contract["capabilities"]
    assert "webui.agent.v1" in contract["capabilities"]


def test_current_producer_declares_binary_attachments():
    contract = webui_contract()
    assert "webui.attachments.binary.v1" in contract["capabilities"]


@pytest.mark.parametrize("version", ["0.0.1", "9.99.0", "0.3.5.dev17", "custom-build"])
def test_independently_released_hosts_share_a_protocol(version):
    report = assess_webui_contract({**webui_contract(), "version": version})
    assert report["status"] == "compatible"
    assert report["host_version"] == version
    assert compatibility_error(report) == ""


@pytest.mark.parametrize("value", [None, {}, "1", {"min_protocol": True, "max_protocol": 1},
    {"min_protocol": 2, "max_protocol": 1, "capabilities": []},
    {"min_protocol": 1, "max_protocol": 1, "capabilities": "webui.core.v1"},
    {"min_protocol": 1, "max_protocol": 1, "capabilities": [123]},
])
def test_missing_or_malformed_metadata_is_unknown_not_outdated(value):
    report = assess_webui_contract(value)
    assert report["status"] == "unknown"
    assert compatibility_error(report) == "webui_compatibility_unknown"


def test_future_optional_protocol_range_can_still_include_current_client():
    assert assess_webui_contract({**webui_contract(), "max_protocol": 3})["status"] == "compatible"


def test_recovery_targets_the_correct_machine():
    future = {**webui_contract(), "min_protocol": 2, "max_protocol": 2}
    assert compatibility_error(assess_webui_contract(future)) == "client_update_required"
    missing = {**webui_contract(), "capabilities": []}
    assert compatibility_error(assess_webui_contract(missing)) == "host_update_required"


def test_unknown_optional_capabilities_do_not_break_connection():
    value = webui_contract()
    value["capabilities"] = ["webui.core.v1", "a-future-optional-feature"]
    assert assess_webui_contract(value)["status"] == "compatible"
