"""Version control: local git plus database snapshots.

Public surface::

    from papercreator.vcs import git, versions

    git.init_repo(path); git.commit(path, "message"); git.status(path)
    versions.timeline(project_id)          # merged commits + snapshots
    versions.compare(project_id, left, right)
    versions.save_version(project_id, label="v1")
    versions.restore_version(project_id, ref)
    versions.file_history(project_id, "introduction")

Git is the system binary, so the same repository opens normally in any other git
tool. Destructive operations are either not exposed or require explicit
confirmation, and every git call is scoped to a directory inside the workspace.

See ``docs/systems/version_control.md``.
"""

from . import git, versions  # noqa: F401

__all__ = ["git", "versions"]
