# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Pre-run hook execution (formerly project_setup) for Sentinel.

Reads the new top-level ``pre_run`` block of the YAML config:

    pre_run:
      enabled: bool
      scripts: [str]            # auxiliary scripts run first, in order
      program: str              # main executable, run last
      args: [str]               # arguments passed to program
      timeout_seconds: int      # per-command timeout (default 1800)

The timeout applies independently to each script and to the program.
A hung hook on one project no longer freezes the rest of a nightly
batch: TimeoutExpired propagates as a phase failure and
``main.execute_phases`` honours ``global_settings.continue_on_error``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .run_context import RunContext


# Default per-command timeout for pre-run hooks (in seconds). 30 minutes
# is generous for vendor environment scripts and register-map generators
# while still bounding a runaway nightly batch.
DEFAULT_PRE_RUN_TIMEOUT_SECONDS = 1800


def _script_command(script: Path) -> List[str]:
    """Return the command list required to execute *script* on the host OS."""
    suffix = script.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script)]

    if os.name == "nt":  # Windows
        if suffix == ".ps1":
            return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        return ["cmd", "/c", str(script)]

    # POSIX shells
    if suffix in {".sh", ""}:
        return ["bash", str(script)]

    return [str(script)]


def _run_command(
    command: List[str], cwd: Path, logger: logging.Logger, timeout: int,
) -> None:
    logger.debug("Executing command: %s (timeout=%ds)", " ".join(command), timeout)
    subprocess.run(command, cwd=cwd, check=True, timeout=timeout)


def _run_pre_scripts(
    scripts: Iterable[str], working_dir: Path, logger: logging.Logger, timeout: int,
) -> Tuple[int, int]:
    """Run every configured pre-run script in order.

    Returns ``(ran, configured)``: ``configured`` is the number of
    entries the operator listed in ``pre_run.scripts``; ``ran`` is
    how many of them actually executed (i.e. their resolved path
    existed AND the subprocess returned 0). Missing-on-disk scripts
    log a warning and are skipped; ``ran`` does not count them.
    Failed or timed-out scripts raise out of the loop and never
    reach the return.
    """
    configured = 0
    ran = 0
    for script in scripts:
        configured += 1
        script_path = (
            Path(script) if os.path.isabs(script) else (working_dir / script).resolve()
        )
        if not script_path.exists():
            logger.warning("Pre-run script missing: %s", script_path)
            continue
        try:
            logger.info("Running pre-run script: %s", script_path)
            _run_command(
                _script_command(script_path), script_path.parent, logger, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "Pre-run script %s timed out after %ds", script_path, timeout,
            )
            raise
        except subprocess.CalledProcessError as exc:
            logger.error("Script %s failed with exit code %s", script_path, exc.returncode)
            raise
        ran += 1
    return ran, configured


def _resolve_working_dir(ctx: RunContext) -> Path:
    """Default working directory for pre_run scripts.

    Use the freshly fetched repo when available, and fall back to the run
    dir otherwise. Note this is *not* ``ctx.repo_root``, which falls back
    to ``<run_dir>/repos`` — pre_run wants the run dir itself when nothing
    has been fetched.
    """
    return ctx.repo_path if ctx.repo_path is not None else ctx.run_dir


def project_setup(config: Dict[str, Any], ctx: RunContext) -> None:
    """Execute optional pre-run hooks before the main phases."""
    logger = logging.getLogger(__name__)

    pre_run = config.get("pre_run") or {}
    if not pre_run.get("enabled", False):
        logger.info("Pre-run phase disabled in configuration")
        return

    working_dir = _resolve_working_dir(ctx)
    working_dir.mkdir(parents=True, exist_ok=True)

    scripts = pre_run.get("scripts", []) or []
    program = pre_run.get("program")
    args = pre_run.get("args", []) or []
    timeout = pre_run.get("timeout_seconds", DEFAULT_PRE_RUN_TIMEOUT_SECONDS)

    logger.info("Starting pre-run phase in %s (timeout=%ds)", working_dir, timeout)
    ran, configured = _run_pre_scripts(scripts, working_dir, logger, timeout=timeout)

    # Runtime version of the OP-023 trap: validator can only check
    # the schema (the scripts list is non-empty), not whether the
    # paths actually resolve at runtime. If the operator listed N
    # scripts and 0 of them existed on disk, the phase contributed
    # nothing despite enabled=true — surface it as a phase failure
    # so monitoring sees something off.
    if configured > 0 and ran == 0 and not program:
        raise RuntimeError(
            f"pre_run.enabled is true and {configured} script(s) were "
            f"configured but none existed on disk; check the per-script "
            f"warnings above for the resolved paths"
        )

    if not program:
        logger.info(
            "Pre-run phase complete: %d/%d scripts ran, no program configured",
            ran, configured,
        )
        return

    command = [program, *args]
    try:
        logger.info("Running pre-run program: %s", " ".join(command))
        _run_command(command, working_dir, logger, timeout=timeout)
        logger.info("Pre-run program completed successfully")
    except subprocess.TimeoutExpired:
        logger.error(
            "Pre-run program %s timed out after %ds", " ".join(command), timeout,
        )
        raise
    except subprocess.CalledProcessError as exc:
        logger.error("Pre-run program failed with exit code %s", exc.returncode)
        raise
