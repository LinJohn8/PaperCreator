"""Deterministic remote-Git contracts using only local bare repositories.

These tests never contact GitHub or a credential helper.  They exercise the
same subprocess and API-facing helpers used by the desktop, including Windows
paths with spaces and non-ASCII characters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from papercreator.core.errors import ConflictError, ExternalToolError
from papercreator.store import projects as projects_store
from papercreator.vcs import git as git_module


pytestmark = pytest.mark.skipif(
    not git_module.git_available(), reason="Git is not installed"
)


def make_bare_remote(root: Path, name: str = "远程 仓库.git") -> Path:
    remote = root / name
    remote.mkdir(parents=True)
    git_module.run(["init", "--bare"], remote, check=True)
    # Bare init follows the host's init.defaultBranch.  PaperCreator projects
    # deliberately use main, so pin the remote HEAD as well to keep clones
    # independent of global Git configuration.
    git_module.run(
        ["symbolic-ref", "HEAD", "refs/heads/main"], remote, check=True
    )
    return remote


def test_local_remote_with_spaces_round_trips_and_pushes(temp_home, project):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    remote = make_bare_remote(temp_home)

    configured = git_module.set_remote(directory, str(remote))
    assert configured == {"remote": "origin", "url": str(remote)}

    info = git_module.remote_info(directory)
    assert info["remotes"] == [{
        "name": "origin",
        "fetch": str(remote),
        "push": str(remote),
        "fetch_urls": [str(remote)],
        "push_urls": [str(remote)],
    }]

    pushed = git_module.push(directory)
    assert pushed["pushed"] is True
    assert pushed["remote"] == "origin"
    assert git_module.run(
        ["show-ref", "--verify", "refs/heads/main"], remote
    ).ok
    fetched = git_module.fetch(directory)
    assert fetched["sync"]["state"] == "up_to_date"


def test_removing_remote_preserves_local_history(temp_home, project):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    remote = make_bare_remote(temp_home, "remove remote.git")
    git_module.set_remote(directory, str(remote))
    before = git_module.run(["rev-parse", "HEAD"], directory, check=True).stdout.strip()

    removed = git_module.remove_remote(directory)

    assert removed["removed"] is True
    assert removed["remote"] == "origin"
    assert git_module.remote_info(directory) == {"remotes": []}
    assert git_module.run(["rev-parse", "HEAD"], directory, check=True).stdout.strip() == before
    assert git_module.status(directory)["clean"] is True


def test_fetch_observes_peer_and_fast_forward_updates_clean_tree(
    temp_home, project
):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    shared = directory / "remote-contract.md"
    shared.write_text("baseline\n", encoding="utf-8")
    git_module.commit(directory, "remote baseline")
    remote = make_bare_remote(temp_home, "fast forward 远程.git")
    git_module.set_remote(directory, str(remote))
    git_module.push(directory)

    peer = temp_home / "fast forward peer"
    git_module.run(["clone", str(remote), str(peer)], temp_home, check=True)
    git_module.run(["config", "user.name", "Remote Peer"], peer, check=True)
    git_module.run(["config", "user.email", "peer@localhost"], peer, check=True)
    (peer / shared.name).write_text("peer update\n", encoding="utf-8")
    git_module.run(["add", shared.name], peer, check=True)
    git_module.run(["commit", "-m", "peer update"], peer, check=True)
    git_module.run(["push", "origin", "main"], peer, check=True)

    fetched = git_module.fetch(directory)
    assert fetched["sync"] == {
        "remote": "origin",
        "branch": "main",
        "tracking_ref": "refs/remotes/origin/main",
        "remote_branch_exists": True,
        "ahead": 0,
        "behind": 1,
        "diverged": False,
        "can_fast_forward": True,
        "state": "behind",
    }
    assert shared.read_text(encoding="utf-8") == "baseline\n"

    pulled = git_module.fast_forward(directory)
    assert pulled["updated"] is True
    assert pulled["commits"] == 1
    assert pulled["sync"]["state"] == "up_to_date"
    assert shared.read_text(encoding="utf-8") == "peer update\n"


def test_fast_forward_refuses_dirty_tree_without_changing_files(temp_home, project):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    shared = directory / "dirty-contract.md"
    shared.write_text("committed\n", encoding="utf-8")
    git_module.commit(directory, "dirty baseline")
    remote = make_bare_remote(temp_home, "dirty remote.git")
    git_module.set_remote(directory, str(remote))
    git_module.push(directory)

    shared.write_text("uncommitted local text\n", encoding="utf-8")
    with pytest.raises(ConflictError, match="not clean"):
        git_module.fast_forward(directory)
    assert shared.read_text(encoding="utf-8") == "uncommitted local text\n"


def test_remote_credentials_are_never_reflected(temp_home, project):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    secret = "not-a-real-password"
    raw = f"https://researcher:{secret}@example.invalid/paper.git"

    configured = git_module.set_remote(directory, raw, name="private")
    info = git_module.remote_info(directory)

    assert secret not in str(configured)
    assert secret not in str(info)
    assert configured["url"] == "https://researcher:***@example.invalid/paper.git"
    assert info["remotes"][0]["fetch"] == configured["url"]


def test_push_rejects_non_fast_forward_without_changing_local_files(
    temp_home, project
):
    directory = projects_store.project_root(project)
    git_module.init_repo(directory)
    shared = directory / "remote-contract.md"
    shared.write_text("baseline\n", encoding="utf-8")
    git_module.commit(directory, "remote baseline")
    remote = make_bare_remote(temp_home, "non fast forward.git")
    git_module.set_remote(directory, str(remote))
    git_module.push(directory)

    peer = temp_home / "peer clone"
    git_module.run(["clone", str(remote), str(peer)], temp_home, check=True)
    git_module.run(["config", "user.name", "Remote Peer"], peer, check=True)
    git_module.run(["config", "user.email", "peer@localhost"], peer, check=True)
    peer_file = peer / shared.name
    peer_file.write_text("peer change\n", encoding="utf-8")
    git_module.run(["add", shared.name], peer, check=True)
    git_module.run(["commit", "-m", "peer change"], peer, check=True)
    git_module.run(["push", "origin", "main"], peer, check=True)

    shared.write_text("local divergent change\n", encoding="utf-8")
    git_module.commit(directory, "local divergent change")
    with pytest.raises(ExternalToolError, match="push failed"):
        git_module.push(directory)

    fetched = git_module.fetch(directory)
    assert fetched["sync"]["state"] == "diverged"
    assert fetched["sync"]["ahead"] == 1
    assert fetched["sync"]["behind"] == 1
    with pytest.raises(ConflictError, match="history have diverged"):
        git_module.fast_forward(directory)

    assert shared.read_text(encoding="utf-8") == "local divergent change\n"
    assert git_module.status(directory)["clean"] is True
