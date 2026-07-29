"""Git wrapper for project repositories.

The requirement is local git commits from inside the workbench. This shells out
to the system ``git`` rather than embedding a library, because the user must be
able to open the same repository in any other tool - GitHub Desktop, VS Code, the
command line - and see normal history.

Safety rules enforced here, all of them because this operates on the user's
manuscript:

* every command is scoped to a project directory that must sit inside the
  configured workspace;
* ``GIT_TERMINAL_PROMPT=0`` so a credential prompt can never hang the backend;
* history-rewriting and destructive operations (``reset --hard``, ``clean -f``,
  force push) are not exposed at all - :func:`discard_changes` restores files but
  refuses without an explicit confirmation flag;
* the user's global git identity is never modified; a repo-local identity is set
  only when no identity is configured, because a commit would otherwise fail.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import ConflictError, ExternalToolError, ValidationError
from ..core.logging_setup import get_logger, scrub
from ..core.paths import get_paths
from ..core.util import utc_now_iso

log = get_logger(__name__)

DEFAULT_IDENTITY = ("PaperCreator", "papercreator@localhost")

_URL_CREDENTIALS = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@"
)


def _safe_remote_text(value: str) -> str:
    """Redact URL-embedded passwords/tokens before returning or logging Git text.

    Git accepts ``https://user:password@host/repo`` remotes.  The credential is
    needed in ``.git/config`` for legacy setups, but it must never be reflected
    through the API, command diagnostics or logs.  SSH-style ``git@host:path``
    and ordinary local paths are intentionally unchanged.
    """

    def replace(match: re.Match[str]) -> str:
        userinfo = match.group("userinfo")
        if ":" in userinfo:
            username = userinfo.split(":", 1)[0]
            return f'{match.group("prefix")}{username}:***@'
        return match.group(0)

    return scrub(_URL_CREDENTIALS.sub(replace, value))


@dataclass
class GitResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    command: str


def git_available() -> bool:
    return shutil.which("git") is not None


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        # A prompt in a subprocess is an indefinite hang, not an error.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "echo",
        # Deterministic, parseable output regardless of the user's locale.
        "LC_ALL": "C",
        "GIT_PAGER": "cat",
    })
    return env


def run(
    args: list[str], cwd: Path, *, timeout: float = 120.0, check: bool = False
) -> GitResult:
    """Run one git command in ``cwd``."""
    if not git_available():
        raise ExternalToolError(
            "git is not installed or not on PATH. Install git to use version "
            "control, or disable it for this project."
        )
    command = _safe_remote_text("git " + " ".join(args))
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout,
            check=False, env=_git_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExternalToolError(f"{command} failed: {exc}") from exc
    result = GitResult(
        ok=completed.returncode == 0,
        stdout=_safe_remote_text(completed.stdout.decode("utf-8", "replace")),
        stderr=_safe_remote_text(completed.stderr.decode("utf-8", "replace")),
        returncode=completed.returncode,
        command=command,
    )
    if check and not result.ok:
        raise ExternalToolError(
            f"{command} exited {result.returncode}",
            details={"stderr": result.stderr[:600], "cwd": str(cwd)},
        )
    return result


def _assert_in_workspace(directory: Path) -> None:
    """Refuse to operate outside the configured workspace.

    Guards against a relocated or hand-edited project path turning a git
    operation into an action on an unrelated repository.
    """
    workspace = get_paths().workspace.resolve()
    try:
        inside = directory.resolve().is_relative_to(workspace)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ConflictError(
            f"refusing to run git in '{directory}': it is outside the workspace "
            f"'{workspace}'",
            details={"path": str(directory), "workspace": str(workspace)},
        )


def is_repo(directory: Path) -> bool:
    if not directory.is_dir() or not git_available():
        return False
    result = run(["rev-parse", "--is-inside-work-tree"], directory)
    return result.ok and result.stdout.strip() == "true"


def init_repo(directory: Path, *, initial_commit: bool = True) -> dict[str, Any]:
    """Initialise a repository, with an initial commit if there is anything to add."""
    _assert_in_workspace(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if is_repo(directory):
        return {"created": False, "path": str(directory), "reason": "already a repo"}

    # `main` explicitly: git's default branch name varies by version and config.
    result = run(["init", "-b", "main"], directory)
    if not result.ok:
        # Older git does not support -b.
        run(["init"], directory, check=True)
        run(["checkout", "-b", "main"], directory)

    _ensure_identity(directory)
    committed = False
    if initial_commit:
        run(["add", "-A"], directory)
        status = run(["status", "--porcelain"], directory)
        if status.stdout.strip():
            commit = run(
                ["commit", "-m", "Initial commit (PaperCreator project)"], directory
            )
            committed = commit.ok
    log.info("initialised git repo at %s (initial commit: %s)", directory, committed)
    return {"created": True, "path": str(directory), "initial_commit": committed}


def _ensure_identity(directory: Path) -> None:
    """Set a repo-local identity only if none is configured.

    A commit fails outright without user.name/user.email. Setting it locally (not
    globally) keeps the user's own identity untouched everywhere else, and lets
    them override it for this repo at any time.
    """
    name = run(["config", "user.name"], directory)
    email = run(["config", "user.email"], directory)
    if not name.stdout.strip():
        run(["config", "user.name", DEFAULT_IDENTITY[0]], directory)
        log.info("set repo-local git user.name for %s", directory)
    if not email.stdout.strip():
        run(["config", "user.email", DEFAULT_IDENTITY[1]], directory)


def status(directory: Path) -> dict[str, Any]:
    """Working tree state, parsed into something a UI can render."""
    if not is_repo(directory):
        return {
            "is_repo": False, "path": str(directory),
            "git_available": git_available(),
        }
    # Enumerate actual files instead of Git's default directory-collapsed
    # ``notes/`` form. The desktop confirmation dialog must show the precise
    # untracked items it promises to preserve before a destructive discard.
    porcelain = run(
        ["status", "--porcelain=v1", "-b", "--untracked-files=all"], directory
    )
    branch = ""
    ahead = behind = 0
    staged: list[dict[str, str]] = []
    unstaged: list[dict[str, str]] = []
    untracked: list[str] = []
    conflicted: list[str] = []

    for line in porcelain.stdout.splitlines():
        if line.startswith("##"):
            header = line[2:].strip()
            branch = header.split("...")[0].strip()
            if "[ahead " in header:
                ahead = int(header.split("[ahead ")[1].split("]")[0].split(",")[0])
            if "behind " in header:
                behind = int(header.split("behind ")[1].split("]")[0].strip())
            continue
        if len(line) < 3:
            continue
        index_state, worktree_state, path = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
        elif "U" in (index_state, worktree_state):
            conflicted.append(path)
        else:
            if index_state != " ":
                staged.append({"status": index_state, "path": path})
            if worktree_state != " ":
                unstaged.append({"status": worktree_state, "path": path})

    last = log_entries(directory, limit=1)
    return {
        "is_repo": True,
        "path": str(directory),
        "git_available": True,
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicted": conflicted,
        "clean": not (staged or unstaged or untracked or conflicted),
        "last_commit": last[0] if last else None,
        "has_remote": bool(run(["remote"], directory).stdout.strip()),
    }


def commit(
    directory: Path,
    message: str,
    *,
    paths: list[str] | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Stage and commit.

    ``paths`` stages specific files; omitting it stages everything, which is what
    "commit my manuscript" means in this app. An empty commit is refused unless
    asked for, because it is almost always a mistake.
    """
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(
            f"'{directory}' is not a git repository. Initialise it first."
        )
    if not message.strip():
        raise ValidationError("a commit needs a message")
    _ensure_identity(directory)

    if paths:
        run(["add", "--", *paths], directory, check=True)
    else:
        run(["add", "-A"], directory, check=True)

    staged = run(["diff", "--cached", "--name-only"], directory)
    if not staged.stdout.strip() and not allow_empty:
        return {
            "committed": False,
            "reason": "nothing to commit - the working tree matches the last commit",
        }

    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    result = run(args, directory)
    if not result.ok:
        raise ExternalToolError(
            "the commit failed",
            details={"stderr": result.stderr[:600], "stdout": result.stdout[:600]},
        )
    head = run(["rev-parse", "HEAD"], directory)
    files = [f for f in staged.stdout.splitlines() if f.strip()]
    log.info("committed %s file(s) in %s", len(files), directory)
    return {
        "committed": True,
        "commit": head.stdout.strip()[:40],
        "message": message,
        "files": files,
        "file_count": len(files),
    }


def log_entries(directory: Path, *, limit: int = 50, path: str = "") -> list[dict[str, Any]]:
    """Commit history. ``path`` limits it to one file's history."""
    if not is_repo(directory):
        return []
    # A unit separator between fields and a record separator between commits:
    # commit subjects can contain anything, including tabs and newlines.
    fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e"
    args = ["log", f"--pretty=format:{fmt}", f"-{max(1, limit)}"]
    if path:
        args.extend(["--", path])
    result = run(args, directory)
    if not result.ok:
        return []
    entries: list[dict[str, Any]] = []
    for record in result.stdout.split("\x1e"):
        if not record.strip():
            continue
        fields = record.strip("\n").split("\x1f")
        if len(fields) < 6:
            continue
        entries.append({
            "hash": fields[0],
            "short": fields[1],
            "author": fields[2],
            "email": fields[3],
            "date": fields[4],
            "subject": fields[5],
            "body": fields[6].strip() if len(fields) > 6 else "",
        })
    return entries


def diff(
    directory: Path,
    *,
    ref: str = "",
    path: str = "",
    staged: bool = False,
    context: int = 3,
) -> dict[str, Any]:
    """Unified diff of the working tree, the index, or against a ref."""
    if not is_repo(directory):
        return {"is_repo": False, "diff": ""}
    args = ["diff", f"-U{max(0, context)}"]
    if staged:
        args.append("--cached")
    if ref:
        args.append(ref)
    if path:
        args.extend(["--", path])
    result = run(args, directory)
    stat_args = ["diff", "--stat"]
    if staged:
        stat_args.append("--cached")
    if ref:
        stat_args.append(ref)
    stat = run(stat_args, directory)
    return {
        "is_repo": True,
        "diff": result.stdout,
        "stat": stat.stdout.strip(),
        "ref": ref or ("index" if staged else "worktree"),
        "path": path,
    }


def show_file(directory: Path, ref: str, path: str) -> str:
    """A file's content at a given ref - used for side-by-side version compare."""
    if not is_repo(directory):
        return ""
    result = run(["show", f"{ref}:{path}"], directory)
    return result.stdout if result.ok else ""


def list_branches(directory: Path) -> dict[str, Any]:
    if not is_repo(directory):
        return {"branches": [], "current": ""}
    result = run(
        ["branch", "--format=%(refname:short)%09%(HEAD)%09%(objectname:short)"],
        directory,
    )
    branches: list[dict[str, Any]] = []
    current = ""
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        is_current = len(parts) > 1 and parts[1].strip() == "*"
        branches.append({
            "name": parts[0].strip(),
            "current": is_current,
            "head": parts[2].strip() if len(parts) > 2 else "",
        })
        if is_current:
            current = parts[0].strip()
    return {"branches": branches, "current": current}


def create_branch(directory: Path, name: str, *, checkout: bool = True) -> dict[str, Any]:
    """Create a branch - the natural way to try a risky rewrite of a section."""
    _assert_in_workspace(directory)
    if not name.strip():
        raise ValidationError("a branch needs a name")
    safe = name.strip().replace(" ", "-")
    args = ["checkout", "-b", safe] if checkout else ["branch", safe]
    result = run(args, directory)
    if not result.ok:
        raise ExternalToolError(
            f"could not create branch '{safe}'",
            details={"stderr": result.stderr[:400]},
        )
    return {"branch": safe, "checked_out": checkout}


def checkout(directory: Path, ref: str) -> dict[str, Any]:
    """Switch branches. Refuses on a dirty tree rather than risking a conflict."""
    _assert_in_workspace(directory)
    state = status(directory)
    if not state.get("clean"):
        raise ConflictError(
            "there are uncommitted changes, so switching would risk losing them. "
            "Commit first, or discard the changes explicitly.",
            details={"unstaged": state.get("unstaged", [])[:10],
                     "untracked": state.get("untracked", [])[:10]},
        )
    result = run(["checkout", ref], directory)
    if not result.ok:
        raise ExternalToolError(
            f"could not check out '{ref}'", details={"stderr": result.stderr[:400]}
        )
    return {"ref": ref, "branch": list_branches(directory)["current"]}


def restore_file(directory: Path, path: str, *, ref: str = "HEAD") -> dict[str, Any]:
    """Restore one file from a ref. Bounded and revertable, unlike a hard reset."""
    _assert_in_workspace(directory)
    result = run(["checkout", ref, "--", path], directory)
    if not result.ok:
        raise ExternalToolError(
            f"could not restore '{path}' from {ref}",
            details={"stderr": result.stderr[:400]},
        )
    return {"restored": path, "from": ref}


def discard_changes(directory: Path, *, confirm: bool = False) -> dict[str, Any]:
    """Discard all uncommitted changes. Requires explicit confirmation.

    Deliberately not a thin wrapper on ``reset --hard``: tracked files are
    restored, untracked files are *left alone*. Deleting untracked files would
    remove figures and notes the user just added, which is unrecoverable.
    """
    _assert_in_workspace(directory)
    if not confirm:
        state = status(directory)
        raise ConflictError(
            "discarding changes is irreversible. Re-issue with confirm=true if "
            "that is intended.",
            details={
                "would_discard": state.get("unstaged", []) + state.get("staged", []),
                "untracked_kept": state.get("untracked", []),
            },
        )
    run(["reset", "HEAD"], directory)
    result = run(["checkout", "--", "."], directory)
    return {
        "discarded": result.ok,
        "note": "tracked files were restored to HEAD; untracked files were kept",
    }


def backup_worktree_patch(directory: Path, target: Path) -> dict[str, Any]:
    """Save all tracked staged/unstaged changes as a recoverable binary patch.

    ``git diff --binary HEAD`` includes both index and working-tree changes and
    embeds changed binary blobs.  The destination must remain inside the
    project so a caller cannot turn this helper into an arbitrary file write.
    Untracked files need no backup because :func:`discard_changes` keeps them.
    """
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    try:
        inside_project = target.resolve().is_relative_to(directory.resolve())
    except (OSError, ValueError):
        inside_project = False
    if not inside_project:
        raise ValidationError(
            f"refusing to write a Git recovery patch outside '{directory}'"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as stream:
            completed = subprocess.run(
                ["git", "diff", "--binary", "HEAD"],
                cwd=str(directory), stdout=stream, stderr=subprocess.PIPE,
                timeout=120.0, check=False, env=_git_env(),
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        target.unlink(missing_ok=True)
        raise ExternalToolError(f"could not create Git recovery patch: {exc}") from exc
    if completed.returncode != 0:
        target.unlink(missing_ok=True)
        raise ExternalToolError(
            "could not create Git recovery patch",
            details={"stderr": scrub(completed.stderr.decode("utf-8", "replace"))[:600]},
        )
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "empty": target.stat().st_size == 0,
        "restore_command": (
            "git apply --index \""
            + target.relative_to(directory).as_posix()
            + "\""
        ),
    }


def remote_info(directory: Path) -> dict[str, Any]:
    if not is_repo(directory):
        return {"remotes": []}
    # Do not parse ``git remote -v`` by whitespace: valid local Windows remotes
    # can contain spaces (and commonly do when the workbench path does).  Ask Git
    # for each URL directly so the returned value round-trips exactly.
    remotes: list[dict[str, Any]] = []
    for name in run(["remote"], directory).stdout.splitlines():
        name = name.strip()
        if not name:
            continue
        fetch_urls = [
            _safe_remote_text(url) for url in
            run(["remote", "get-url", "--all", name], directory).stdout.splitlines()
            if url.strip()
        ]
        push_urls = [
            _safe_remote_text(url) for url in
            run(["remote", "get-url", "--push", "--all", name], directory).stdout.splitlines()
            if url.strip()
        ]
        remotes.append({
            "name": name,
            "fetch": fetch_urls[0] if fetch_urls else "",
            "push": push_urls[0] if push_urls else "",
            "fetch_urls": fetch_urls,
            "push_urls": push_urls,
        })
    return {"remotes": remotes}


def _require_remote(directory: Path, name: str) -> str:
    """Return a configured remote name, rejecting option-like API input."""
    candidate = name.strip()
    if not candidate or candidate.startswith("-"):
        raise ValidationError("a configured Git remote name is required")
    existing = [item.strip() for item in run(["remote"], directory).stdout.splitlines()]
    if candidate not in existing:
        raise ValidationError(f"Git remote '{candidate}' is not configured")
    return candidate


def _current_branch(directory: Path) -> str:
    branch = list_branches(directory)["current"]
    if not branch:
        raise ConflictError(
            "the Git repository is in detached HEAD state. Check out a local "
            "branch before synchronising with a remote."
        )
    return branch


def _raise_remote_failure(action: str, result: GitResult) -> None:
    combined = f"{result.stderr}\n{result.stdout}"
    auth_markers = (
        "could not read Username", "Authentication failed",
        "Permission denied (publickey)", "terminal prompts disabled",
    )
    if any(marker.lower() in combined.lower() for marker in auth_markers):
        raise ExternalToolError(
            f"the {action} needs credentials, and this backend cannot prompt for "
            "them. Configure a Git credential helper (or an SSH key) and try again.",
            details={"stderr": result.stderr[:400]},
        )
    raise ExternalToolError(
        f"the {action} failed", details={"stderr": result.stderr[:600]}
    )


def remote_sync_status(
    directory: Path, *, remote: str = "origin", branch: str = ""
) -> dict[str, Any]:
    """Compare local HEAD with an already-fetched remote-tracking branch.

    This function never contacts the network and never changes the working tree.
    ``ahead`` is local-only commits; ``behind`` is remote-only commits.
    """
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    remote = _require_remote(directory, remote)
    target = branch.strip() or _current_branch(directory)
    if target.startswith("-"):
        raise ValidationError("the Git branch name is invalid")
    tracking_ref = f"refs/remotes/{remote}/{target}"
    exists = run(["show-ref", "--verify", tracking_ref], directory).ok
    result: dict[str, Any] = {
        "remote": remote,
        "branch": target,
        "tracking_ref": tracking_ref,
        "remote_branch_exists": exists,
        "ahead": 0,
        "behind": 0,
        "diverged": False,
        "can_fast_forward": False,
    }
    if not exists:
        result["state"] = "unpublished"
        return result
    counts = run(
        ["rev-list", "--left-right", "--count", f"HEAD...{tracking_ref}"],
        directory,
        check=True,
    ).stdout.split()
    if len(counts) != 2:
        raise ExternalToolError("Git returned an unreadable remote divergence count")
    ahead, behind = int(counts[0]), int(counts[1])
    result.update({
        "ahead": ahead,
        "behind": behind,
        "diverged": ahead > 0 and behind > 0,
        "can_fast_forward": ahead == 0 and behind > 0,
        "state": (
            "diverged" if ahead > 0 and behind > 0
            else "ahead" if ahead > 0
            else "behind" if behind > 0
            else "up_to_date"
        ),
    })
    return result


def set_remote(directory: Path, url: str, *, name: str = "origin") -> dict[str, Any]:
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    if not url.strip():
        raise ValidationError("a Git remote URL or local repository path is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name.strip()):
        raise ValidationError("the Git remote name contains unsupported characters")
    name = name.strip()
    existing = run(["remote"], directory).stdout.split()
    args = (
        ["remote", "set-url", name, url] if name in existing
        else ["remote", "add", name, url]
    )
    result = run(args, directory)
    if not result.ok:
        raise ExternalToolError(
            f"could not set remote '{name}'", details={"stderr": result.stderr[:400]}
        )
    return {"remote": name, "url": _safe_remote_text(url)}


def remove_remote(directory: Path, *, name: str = "origin") -> dict[str, Any]:
    """Remove one configured remote without touching local refs or commits."""
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    remote = _require_remote(directory, name)
    result = run(["remote", "remove", remote], directory)
    if not result.ok:
        raise ExternalToolError(
            f"could not remove remote '{remote}'",
            details={"stderr": result.stderr[:400]},
        )
    return {
        "removed": True,
        "remote": remote,
        "note": "local commits, branches and working files were preserved",
    }


def fetch(directory: Path, *, remote: str = "origin") -> dict[str, Any]:
    """Fetch and prune one configured remote without changing local files."""
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    remote = _require_remote(directory, remote)
    branch = _current_branch(directory)
    result = run(["fetch", "--prune", remote], directory, timeout=300)
    if not result.ok:
        _raise_remote_failure("fetch", result)
    return {
        "fetched": True,
        "output": result.stdout.strip() or result.stderr.strip(),
        "sync": remote_sync_status(directory, remote=remote, branch=branch),
    }


def fast_forward(directory: Path, *, remote: str = "origin") -> dict[str, Any]:
    """Fast-forward the current clean branch to an already-fetched remote ref.

    Deliberately refuses dirty, local-ahead and diverged repositories. It never
    starts a merge and therefore cannot leave conflict markers in a manuscript.
    Callers that mirror files into a database must take their recovery material
    before calling this function and re-index after it succeeds.
    """
    _assert_in_workspace(directory)
    state = status(directory)
    if not state.get("is_repo"):
        raise ValidationError(f"'{directory}' is not a git repository")
    if not state.get("clean"):
        raise ConflictError(
            "the Git working tree is not clean. Commit or explicitly discard local "
            "changes before pulling remote work.",
            details={
                "staged": state.get("staged", [])[:10],
                "unstaged": state.get("unstaged", [])[:10],
                "untracked": state.get("untracked", [])[:10],
                "conflicted": state.get("conflicted", [])[:10],
            },
        )
    sync = remote_sync_status(directory, remote=remote)
    if not sync["remote_branch_exists"]:
        raise ValidationError(
            f"remote branch '{sync['remote']}/{sync['branch']}' does not exist; "
            "push the local branch first"
        )
    if sync["diverged"]:
        raise ConflictError(
            "local and remote history have diverged. PaperCreator will not merge "
            "or rewrite the manuscript automatically; resolve the branches in a "
            "Git client, then fetch again.",
            details={"ahead": sync["ahead"], "behind": sync["behind"]},
        )
    if sync["ahead"]:
        return {
            "updated": False,
            "reason": "local branch is ahead; push it before pulling",
            "sync": sync,
        }
    if not sync["behind"]:
        return {"updated": False, "reason": "already up to date", "sync": sync}
    result = run(["merge", "--ff-only", sync["tracking_ref"]], directory)
    if not result.ok:
        _raise_remote_failure("fast-forward pull", result)
    return {
        "updated": True,
        "commits": sync["behind"],
        "remote": sync["remote"],
        "branch": sync["branch"],
        "output": result.stdout.strip() or result.stderr.strip(),
        "sync": remote_sync_status(
            directory, remote=sync["remote"], branch=sync["branch"]
        ),
    }


def push(directory: Path, *, remote: str = "origin", branch: str = "") -> dict[str, Any]:
    """Push to a remote. Never forces.

    Requires a configured credential helper: this deliberately cannot prompt, so
    an unauthenticated push fails fast with an explanation rather than hanging.
    """
    _assert_in_workspace(directory)
    if not is_repo(directory):
        raise ValidationError(f"'{directory}' is not a git repository")
    remote = _require_remote(directory, remote)
    target = branch.strip() or _current_branch(directory)
    if target.startswith("-"):
        raise ValidationError("the Git branch name is invalid")
    result = run(["push", "-u", remote, target], directory, timeout=300)
    if not result.ok:
        _raise_remote_failure("push", result)
    return {"pushed": True, "remote": remote, "branch": target,
            "output": result.stdout.strip() or result.stderr.strip()}


def auto_commit(
    directory: Path, reason: str, *, paths: list[str] | None = None
) -> dict[str, Any]:
    """Commit automatically after a significant change, if configured.

    Used after agent runs and template application. Failures are swallowed into
    the return value: an auto-commit is a convenience, and it must never break the
    operation that triggered it.
    """
    from ..core.config import get_settings

    if not get_settings().writing.auto_git_commit:
        return {"committed": False, "reason": "auto-commit is disabled in settings"}
    if not git_available():
        return {"committed": False, "reason": "git is not installed"}
    if not is_repo(directory):
        return {"committed": False, "reason": "not a git repository"}
    try:
        return commit(
            directory, f"{reason} [auto] {utc_now_iso()}", paths=paths
        )
    except (ExternalToolError, ConflictError, ValidationError) as exc:
        log.warning("auto-commit in %s failed: %s", directory, exc)
        return {"committed": False, "reason": str(exc)}
