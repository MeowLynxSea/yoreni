"""CI workflow regressions that survive independent job scheduling."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


def workflow(name):
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_ci_runs_for_distinct_revisions_do_not_cancel_each_other():
    concurrency = workflow("ci.yml")["concurrency"]
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.ref }}-${{ github.sha }}"
    assert concurrency["cancel-in-progress"] == "true"


def test_windows_installer_matrix_has_an_independent_required_job():
    jobs = workflow("ci.yml")["jobs"]
    installer = jobs["windows_installers"]
    assert installer["runs-on"] == "windows-latest"
    assert "native_required" in installer["if"] and "python_required" in installer["if"]
    assert "github.event_name != 'push'" in installer["if"]
    commands = [step.get("run", "") for step in installer["steps"]]
    assert any("tests/test_install_scripts.py" in command and "-n 4" in command for command in commands)
    assert not any("tests/test_install_scripts.py" in step.get("run", "")
                   for step in jobs["windows_process"]["steps"])
    assert "windows_installers" in jobs["required"]["needs"]
