# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Code fetching utilities for Sentinel runs (YAML schema v1.0)."""

import logging
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .run_context import RunContext

try:  # Prefer GitPython when available to avoid shelling out
    from git import Repo, GitCommandError

    HAS_GITPYTHON = True
except Exception:  # pragma: no cover - GitPython optional dependency
    Repo = None
    GitCommandError = Exception
    HAS_GITPYTHON = False


def handle_remove_readonly(func: Callable, path: str, *args: Any) -> None:
    """Force-remove a readonly file (shutil.rmtree onerror/onexc callback).

    The third positional argument is intentionally ignored: under
    ``onerror=`` (Python < 3.12) it is the ``exc_info`` 3-tuple, under
    ``onexc=`` (Python >= 3.12) it is a single ``exc`` Exception. The
    callback reads neither, so one ``*args`` signature serves both.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _safe_rmtree(path: Any) -> None:
    """Remove a directory tree, forcing readonly files, across versions.

    ``shutil.rmtree``'s ``onerror=`` is deprecated in Python 3.12 for
    ``onexc=`` (different callback signature), and ``onexc=`` does not
    exist before 3.12, so the keyword is selected by version. The
    callback is shared (see :func:`handle_remove_readonly`).
    """
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handle_remove_readonly)
    else:
        shutil.rmtree(path, onerror=handle_remove_readonly)


def fetch_code(config: Dict[str, Any], ctx: RunContext) -> Optional[str]:
    """Clone a Git repo or copy a local folder into ``<run_dir>/repos/``.

    Reads from the new top-level ``fetch`` block of the YAML config:
        fetch:
          type: git|local
          url: ...           # git
          branch: ...        # git
          shallow_clone: ... # git
          depth: ...         # git
          ssh_key_path: ...  # git, optional
          local_path: ...    # local

    Returns the absolute path to the populated directory, or ``None`` on
    failure. The same path is also recorded on ``ctx.repo_path`` for
    downstream phases.
    """
    logger = logging.getLogger(__name__)

    fetch_cfg = config.get("fetch") or {}
    repo_type = (fetch_cfg.get("type") or "").lower()

    # Mark fetch as attempted before any IO so downstream phases can
    # distinguish a failed fetch (partial residue under repos_root,
    # ctx.repo_path still None) from a pre-staged layout that
    # deliberately skipped fetch.
    ctx.fetch_attempted = True

    repo_root = ctx.repos_root
    repo_root.mkdir(parents=True, exist_ok=True)

    if repo_type == "git":
        url = fetch_cfg.get("url")
        if not url:
            logger.error("fetch.url missing for git type")
            return None
        repo_name = os.path.basename(url.rstrip("/")).replace(".git", "") or "code"
        target_path = repo_root / repo_name

        if target_path.exists():
            logger.info("Removing existing repo folder: %s", target_path)
            _safe_rmtree(target_path)

        logger.info("Fetching code into: %s", target_path)

        branch = fetch_cfg.get("branch", "main")
        shallow = fetch_cfg.get("shallow_clone", True)
        depth = fetch_cfg.get("depth", 1)
        ssh_key_path = fetch_cfg.get("ssh_key_path")

        git_ssh_command = _build_git_ssh_command(ssh_key_path, logger)

        if HAS_GITPYTHON:
            clone_kwargs: Dict[str, Any] = {"branch": branch}
            if shallow:
                clone_kwargs.update({"depth": depth, "single_branch": True})

            if git_ssh_command:
                os.environ["GIT_SSH_COMMAND"] = git_ssh_command
            try:
                Repo.clone_from(url, target_path, **clone_kwargs)
                logger.info("Repository cloned with GitPython")
            except GitCommandError as exc:
                logger.error("GitPython clone failed: %s", exc)
                return None
            finally:
                if git_ssh_command:
                    os.environ.pop("GIT_SSH_COMMAND", None)
        else:
            clone_cmd = [
                "git", "clone",
                *(["--depth", str(depth), "--single-branch"] if shallow else []),
                "--branch", branch,
                url,
                str(target_path),
            ]
            env = os.environ.copy()
            if git_ssh_command:
                env["GIT_SSH_COMMAND"] = git_ssh_command
            result = subprocess.run(clone_cmd, capture_output=True, text=True, env=env, check=False)
            if result.returncode != 0:
                logger.error("Git clone failed: %s", result.stderr.strip())
                return None
            logger.info("Repository cloned with system git client")

    elif repo_type == "local":
        local_path = fetch_cfg.get("local_path")
        if not local_path:
            logger.error("fetch.local_path missing for local type")
            return None
        source = Path(os.path.expanduser(local_path))
        if not source.exists():
            logger.error("Local path '%s' does not exist", source)
            return None

        repo_name = source.name or "code"
        target_path = repo_root / repo_name

        if target_path.exists():
            logger.info("Removing existing repo folder: %s", target_path)
            _safe_rmtree(target_path)

        logger.info("Fetching code into: %s", target_path)
        target_path.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            destination = target_path / item.name
            if item.is_dir():
                shutil.copytree(item, destination)
            else:
                shutil.copy2(item, destination)
        logger.info("Local directory copied to run workspace")

    else:
        logger.error("Unsupported fetch.type %r (expected 'git' or 'local')", repo_type)
        return None

    ctx.repo_path = target_path
    logger.info("Code fetch completed successfully")
    return str(target_path)


def _build_git_ssh_command(ssh_key_path: Optional[str], logger: logging.Logger) -> Optional[str]:
    """Build the value for the ``GIT_SSH_COMMAND`` environment variable.

    The result is a shell-style command line that ``git`` parses
    internally (via ``sq_dequote``-style handling) before invoking
    ssh. We use :func:`shlex.quote` on the expanded path so paths
    containing spaces, double quotes, single quotes, ``$``, or other
    shell metacharacters survive intact instead of producing a
    malformed env var that either silently degrades to the default
    SSH agent or — worse — causes ssh to receive an unexpected
    ``-i`` argument.
    """
    if not ssh_key_path:
        return None
    expanded = os.path.expanduser(ssh_key_path)
    if os.path.exists(expanded):
        logger.info("Using dedicated SSH key for Git clone")
        return f"ssh -i {shlex.quote(expanded)} -o IdentitiesOnly=yes"
    logger.warning("Configured SSH key not found; falling back to default agent")
    return None
