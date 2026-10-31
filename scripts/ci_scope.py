#!/usr/bin/env python3
"""Conservative, stdlib-only CI path selection; importable without running git.

Rules describe validation needs, not event policy: the workflow may restrict some
jobs to PRs, but dispatch and fallback still request every validation lane.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

JOBS = ("python_required", "webui_required", "native_required")
ALL = frozenset(JOBS)
PYTHON = frozenset({"python_required"})
WEBUI = frozenset({"webui_required"})
NATIVE = frozenset({"native_required"})

# Ordered first-match rules. Keep embedded channel frontend trees before the
# Python production catch-all.
RULES = (
    ((".github/workflows/ci.yml",
      "scripts/ci_scope.py", "tests/scripts/test_ci_scope.py",
      "scripts/ci_gate.py", "tests/scripts/test_ci_gate.py", "tests/scripts/test_ci_workflows.py"),
     ALL, "CI selector or execution contract"),
    ((".github/workflows/*",), ALL, "unknown workflow"),
    (("pyproject.toml", "uv.lock", "hatch_build.py", "yoreni/webui/build.py", "scripts/install*",
      "scripts/build*"), ALL, "cross-domain dependency, build or installation contract"),
    # Shared fixture consumers live in tests/webui and webui/src/tests; the
    # The SQLite migration fixture has a separate Python owner.
    (("tests/webui/fixtures/runtime-config-keys.json",), PYTHON | WEBUI,
     "shared Python and WebUI runtime configuration test contract"),
    (("tests/session/fixtures/sqlite_v1.sql",), PYTHON, "Python session schema migration fixture"),
    (("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".agent/*", ".github/ISSUE_TEMPLATE/*",
      "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "webui/README.md"),
     frozenset(), "documentation only"),
    (("scripts/check_client_import_boundaries.*",), WEBUI, "shared client import boundary checker"),
    (("packages/client-events/*",), WEBUI, "shared client event protocol"),
    (("webui/*", "yoreni/channels/*/webui/*", "yoreni/channels/*/tests/webui/*"),
     WEBUI, "WebUI source or embedded channel frontend"),
    (("yoreni/channels/websocket/runtime.py", "yoreni/webui/gateway_endpoint.py"),
     PYTHON | WEBUI | NATIVE, "shared gateway lifecycle and client protocol"),
    # These modules own wire events, transport and session representations used
    # by both clients. Other backend WebUI modules need only the WebUI consumer.
    (("yoreni/webui/client_contract.py", "yoreni/webui/outbound_*.py",
      "yoreni/webui/inbound_commands.py", "yoreni/webui/ws_http.py",
      "yoreni/webui/gateway_*.py", "yoreni/webui/session_*.py",
      "yoreni/webui/history.py", "yoreni/webui/transcript.py",
      "yoreni/webui/sidebar_state.py", "yoreni/webui/settings_*.py",
      "yoreni/webui/workspaces.py", "yoreni/webui/skills_api.py",
      "yoreni/webui/cli_apps_api.py", "yoreni/webui/mcp_presets_api.py",
      "yoreni/webui/recovery.py", "yoreni/webui/forking.py", "yoreni/session/*", "yoreni/events.py",
      "yoreni/bus/events.py", "yoreni/channels/websocket/*.py"),
     PYTHON | WEBUI, "shared backend client protocol, transport or session representation"),
    (("yoreni/webui/*",), PYTHON | WEBUI, "WebUI backend"),
    (("yoreni/bun.py",
      "yoreni/build_info.py", "yoreni/__init__.py", "yoreni/__main__.py",
      "yoreni/yoreni.py", "yoreni/process_runtime.py", "yoreni/runtime_admission.py", "yoreni/data_access.py",
      "yoreni/gateway/*", "yoreni/cli/entry.py", "yoreni/cli/commands.py",
      "yoreni/cli/gateway*.py", "yoreni/cli/process_identity.py",
      "yoreni/cli/runtime_config.py", "yoreni/cli/webui.py", "yoreni/cli/webui_support.py",
      "yoreni/config/paths.py", "yoreni/utils/rotating_output.py",
      "yoreni/utils/atomic_file.py", "yoreni/utils/file_io.py", "yoreni/utils/runtime.py",
      "yoreni/utils/restart.py"),
     PYTHON | NATIVE, "installed application lifecycle or runtime"),
    (("yoreni/*",), PYTHON, "Python production source"),
    (("tests/test_install*", "tests/test_bun.py",
      "tests/test_build_info.py", "tests/gateway/*", "tests/cli/test_entry.py",
      "tests/cli/test_commands.py", "tests/cli/test_gateway*", "tests/cli/test_process_identity.py",
      "tests/cli/test_restart*",
      "tests/utils/test_atomic*", "tests/utils/test_file_io*", "tests/test_process*",
      "tests/test_runtime*"), PYTHON | NATIVE, "lifecycle or installation verification"),
    (("tests/*.py", "conftest.py", "scripts/*.py"), PYTHON, "Python tests or tooling"),
)


def classify_paths(paths: list[str]) -> dict:
    """Select the union of requirements, preserving a reason for every path."""
    required = set()
    decisions = []
    for path in paths:
        for patterns, jobs, reason in RULES:
            if any(fnmatchcase(path, pattern) for pattern in patterns):
                break
        else:
            jobs, reason = ALL, "unknown path; conservative full validation"
        required.update(jobs)
        decisions.append({"path": path, "reason": reason, "jobs": sorted(jobs)})
    if not paths:
        required.update(ALL)
    return {
        "outputs": {job: job in required for job in JOBS},
        "reason": "classified changed paths" if paths else "empty diff; conservative full validation",
        "paths": decisions,
    }


def select_scope(event: str, base: str, head: str, *, cwd: Path | None = None) -> dict:
    """Use merge-base diff for PRs, endpoint diff for pushes, full for dispatch."""
    if event == "workflow_dispatch":
        result = classify_paths([])
        result["reason"] = "manual dispatch; full validation"
        return result
    if event not in {"pull_request", "push"}:
        raise ValueError(f"unsupported event: {event}")
    # Accept only commit IDs, never option-like revision expressions from env.
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha) for sha in (base, head)):
        result = classify_paths([])
        result["reason"] = "invalid or missing commit SHA; conservative full validation"
        return result
    diff_range = f"{base}{'...' if event == 'pull_request' else '..'}{head}"
    try:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", diff_range, "--"],
            cwd=cwd, check=True, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result = classify_paths([])
        result["reason"] = f"git diff unavailable ({type(exc).__name__}); conservative full validation"
        return result
    paths = diff.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    result = classify_paths([path for path in paths if path])
    result["diff_range"] = diff_range
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=("pull_request", "push", "workflow_dispatch"))
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    result = select_scope(args.event, args.base, args.head)
    report = json.dumps(result, indent=2, ensure_ascii=True)
    print(report)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            for job, required in result["outputs"].items():
                stream.write(f"{job}={str(required).lower()}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            # JSON escapes path newlines; HTML escaping also prevents Markdown or
            # raw HTML in filenames from escaping the summary's preformatted block.
            stream.write("## CI scope\n\n<pre>" + html.escape(report) + "</pre>\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
