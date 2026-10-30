"""Tests for ``goal_state`` Agent state helpers."""

from __future__ import annotations

from yoreni.agent.goal_state import (
    GOAL_STATE_KEY,
    MAX_GOAL_OBJECTIVE_CHARS,
    explicit_goal_requested,
    goal_state_runtime_lines,
    goal_state_ws_blob,
    parse_goal_state,
    sustained_goal_active,
)


def test_runtime_lines_empty_when_no_metadata():
    assert goal_state_runtime_lines(None) == []
    assert goal_state_runtime_lines({}) == []


def test_runtime_lines_empty_when_completed():
    meta = {
        GOAL_STATE_KEY: {"status": "completed", "objective": "was doing X"},
    }
    assert goal_state_runtime_lines(meta) == []


def test_runtime_lines_include_objective_when_active():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Ship the fix.",
            "ui_summary": "fix",
        },
    }
    lines = goal_state_runtime_lines(meta)
    assert "Goal (active):" in lines
    assert "Ship the fix." in lines
    assert any("Summary: fix" in ln for ln in lines)


def test_runtime_lines_preserve_maximum_accepted_objective():
    objective = "x" * MAX_GOAL_OBJECTIVE_CHARS

    lines = goal_state_runtime_lines(
        {GOAL_STATE_KEY: {"status": "active", "objective": objective}}
    )

    assert lines == ["Goal (active):", objective]








def test_parse_goal_state_accepts_json_string():
    assert parse_goal_state('{"status":"active","objective":"x"}') == {
        "status": "active",
        "objective": "x",
    }


def test_goal_state_ws_blob_inactive_when_missing_or_completed():
    assert goal_state_ws_blob(None) == {"active": False}
    assert goal_state_ws_blob({}) == {"active": False}
    assert goal_state_ws_blob({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}) == {
        "active": False,
    }


def test_goal_state_ws_blob_active_shape():
    meta = {
        GOAL_STATE_KEY: {
            "status": "active",
            "objective": "Build feature.",
            "ui_summary": "feat",
        },
    }
    assert goal_state_ws_blob(meta) == {
        "active": True,
        "status": "active",
        "ui_summary": "feat",
        "objective": "Build feature.",
    }


def test_goal_state_ws_blob_preserves_blocked_state_for_host_attention():
    meta = {
        GOAL_STATE_KEY: {
            "status": "blocked",
            "objective": "Deploy safely.",
            "ui_summary": "Approval required",
            "recap": "Production access is required.",
        },
    }
    assert goal_state_ws_blob(meta) == {
        "active": False,
        "status": "blocked",
        "ui_summary": "Approval required",
        "objective": "Deploy safely.",
        "recap": "Production access is required.",
    }


def test_sustained_goal_active_false_when_missing_or_completed():
    assert sustained_goal_active(None) is False
    assert sustained_goal_active({}) is False
    assert sustained_goal_active({GOAL_STATE_KEY: {"status": "completed", "objective": "x"}}) is False


def test_sustained_goal_active_true_when_active():
    meta = {GOAL_STATE_KEY: {"status": "active", "objective": "Run long task."}}
    assert sustained_goal_active(meta) is True




def test_explicit_goal_requested_reads_goal_metadata():
    assert explicit_goal_requested({}) is False
    assert explicit_goal_requested({"goal_requested": True}) is True
