"""Project paths stay inside the projects root."""

from __future__ import annotations

import shutil

import pytest
from project_helpers import ProjectEnv

from yoreni.projects.scope import ProjectScopeError
from yoreni.projects.service import ProjectError


@pytest.mark.parametrize(
    "bad_id",
    ["", "..", "../../escape", "abc", "a" * 31, "a" * 33, "AB" * 16, "zz" * 16],
)
def test_invalid_project_ids_never_resolve_a_directory(env: ProjectEnv, bad_id: str):
    with pytest.raises(ProjectScopeError):
        env.projects.directory(bad_id)

    assert env.projects.get(bad_id) is None
    with pytest.raises(ProjectScopeError):
        env.projects.ingest_uploads(bad_id, [str(env.workspace / "notes.txt")])


def test_symlinked_project_directory_cannot_escape_the_projects_root(
    env: ProjectEnv, create_symlink, tmp_path_factory,
):
    project = env.create("Linked")
    directory = env.workspace / "projects" / project.id
    shutil.rmtree(directory)
    outside = tmp_path_factory.mktemp("outside-project")
    create_symlink(directory, outside, target_is_directory=True)

    with pytest.raises(ProjectError, match="escaped"):
        env.projects.directory(project.id)
    with pytest.raises(ProjectError, match="escaped"):
        env.projects.ensure_directory(project.id)

    assert not (outside / "uploads").exists()
