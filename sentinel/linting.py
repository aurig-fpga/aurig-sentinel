# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""FPGA HDL linting phase via aurig-lint's project lint runner.

Sentinel does not parse VHDL itself. The linting phase invokes
``tools/run_lint_project_inprocess.tcl`` from the aurig-lint repository
as a subprocess (``tclsh ...``). aurig-lint owns the HDL-aware work:
walking the canonical project manifest, applying rule policy,
emitting HTML/MD/CSV/text reports, and exit codes that Sentinel maps
onto phase statuses.

Distribution model (OP-040 v1)
------------------------------
aurig-lint is cloned manually on the workstation that runs Sentinel and
pointed at via ``phases.linting.aurig_lint_path`` in the YAML, or via
the env var ``SENTINEL_AURIG_LINT_PATH``. Versioned release tarballs
and single-binary (starkit/tclkit) packaging are tracked as future
work.

Exit code contract (from aurig-lint's lint runner)
------------------------------------------------
- ``0`` — clean: no diagnostics at or above the ``fail_on`` threshold.
- ``1`` — diagnostics at or above the threshold; Sentinel maps to
  ``failed`` so ``continue_on_error`` controls whether the next phase
  runs.
- ``2`` — tool error inside aurig-lint / tclsh / the subprocess.
  Sentinel maps to ``error`` (distinct from ``failed``) so callers
  can tell a broken setup from a code-quality regression.
- Any other non-zero code — treated as a tool/setup issue and
  mapped to ``error`` as well, not to ``failed``. Rationale:
  aurig-lint documents only 0/1/2; an unexpected code means either
  a aurig-lint bug, a tclsh crash, or a future contract drift. None
  of those are code-quality regressions, so they should not be
  reported as "lint failed" to the operator.

Baseline workflow (``-baseline`` / ``-only_new`` / ``-update_baseline``)
is supported by the upstream runner but is intentionally not exposed
in the v1 schema; tracking incremental CI baselines via Sentinel
lands as a follow-up OP when a customer asks for it.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .run_context import RunContext


DEFAULT_LINT_TIMEOUT_SECONDS = 1800
AURIG_LINT_RUNNER_REL = ("tools", "run_lint_project_inprocess.tcl")


def _is_safe_relative_path(value: str) -> bool:
    """Return True when *value* is a relative path with at least one
    real segment and no ``..`` segments.

    Used to keep ``output_dir`` from escaping the run dir. The
    validator enforces the same check at config-load time; this is
    the defensive runtime backstop for callers that bypass
    validation (e.g. ad-hoc test fixtures).

    Values like ``""``, ``"."``, ``"./"``, and ``"./."`` are rejected:
    they all normalize to ``Path(".")`` whose ``.parts`` is the
    empty tuple ``()`` in pathlib (verified empirically on Python
    3.8+ — `Path('.').parts` returns ``()``, not ``('.',)``, despite
    what some static analyzers claim). Without this check
    ``ctx.output_dir(".")`` would resolve to ``ctx.run_dir`` and
    ``package_artifacts`` would later attempt to ``copytree`` the
    run dir into ``<run_dir>/artifacts/<run_dir name>``, mixing
    lint output with the run root and breaking the bundle step.
    """
    p = Path(value)
    if p.is_absolute():
        return False
    # Path('.').parts == () in pathlib — this is what catches the
    # ``.`` / ``./`` / ``""`` / ``./.`` family of "would resolve to
    # run_dir itself" inputs. Pinned by
    # TestLintingPhase.test_output_dir_dot_returns_error_at_runtime.
    if not p.parts:
        return False
    return ".." not in p.parts


def run_linting(config: Dict[str, Any], ctx: RunContext) -> Optional[Dict[str, Any]]:
    """Run HDL linting via aurig-lint's project lint runner.

    Reads ``phases.linting`` from the YAML schema and the top-level
    ``project_manifest``. Invokes
    ``tclsh <aurig-lint>/tools/run_lint_project_inprocess.tcl
    -manifest <repo>/<project_manifest>`` with ``-fail_on``,
    ``-format``, ``-outdir`` (and the optional ``-policy``,
    ``-include``, ``-exclude``) passed through from the YAML.

    Returns a status dict consumed by ``sentinel.main.execute_phases``.
    """
    logger = logging.getLogger(__name__)
    lint_config = (config.get("phases") or {}).get("linting") or {}

    if not lint_config.get("enabled", False):
        logger.info("Linting phase disabled in configuration")
        return {"status": "skipped", "message": "Phase disabled"}

    # Resolve aurig_lint_path: YAML field -> env -> targeted error.
    # An empty string in either source counts as missing (treats
    # `aurig_lint_path: ""` and `SENTINEL_AURIG_LINT_PATH=` the same way).
    aurig_lint_path = lint_config.get("aurig_lint_path") or os.environ.get(
        "SENTINEL_AURIG_LINT_PATH"
    )
    if not aurig_lint_path:
        logger.error(
            "Linting: aurig_lint_path not configured "
            "(set phases.linting.aurig_lint_path or env SENTINEL_AURIG_LINT_PATH)"
        )
        return {
            "status": "error",
            "message": (
                "aurig_lint_path missing — set phases.linting.aurig_lint_path "
                "in YAML or env SENTINEL_AURIG_LINT_PATH"
            ),
        }

    aurig_lint_path = os.path.expanduser(aurig_lint_path)
    if not os.path.isdir(aurig_lint_path):
        logger.error(
            "Linting: aurig_lint_path is not a directory: %s", aurig_lint_path
        )
        return {
            "status": "error",
            "message": f"aurig_lint_path not found or not a directory: {aurig_lint_path}",
        }

    runner_script = os.path.join(aurig_lint_path, *AURIG_LINT_RUNNER_REL)
    if not os.path.isfile(runner_script):
        logger.error(
            "Linting: aurig-lint project runner not found at %s — "
            "confirm aurig_lint_path points at the aurig-lint repository root",
            runner_script,
        )
        return {
            "status": "error",
            "message": (
                "aurig-lint lint runner not found at "
                "tools/run_lint_project_inprocess.tcl under aurig_lint_path "
                f"({aurig_lint_path}); confirm the path points at the "
                "aurig-lint repository root"
            ),
        }

    # OP-034 guard, mirroring synthesis: distinguish "fetch ran but
    # produced nothing" from "no fetch attempted and no pre-staged
    # sources" so the operator gets a targeted message instead of a
    # confusing downstream tclsh failure.
    repo_root_path = ctx.repo_root
    if ctx.repo_path is None and (
        ctx.fetch_attempted
        or not repo_root_path.is_dir()
        or not any(repo_root_path.iterdir())
    ):
        if ctx.fetch_attempted:
            logger.error(
                "Linting cannot run: fetch phase ran but produced no "
                "usable repository (see fetch logs)"
            )
            return {
                "status": "error",
                "message": "Fetch phase ran but produced no usable repository",
            }
        logger.error(
            "Linting cannot run: no repository available "
            "(enable the fetch phase or pre-stage sources under repos_root)"
        )
        return {
            "status": "error",
            "message": (
                "No repository available; enable the fetch phase "
                "or pre-stage sources under repos_root"
            ),
        }

    project_manifest = config.get("project_manifest")
    if not project_manifest:
        # validate_config already requires this field; this branch
        # defends against bypassed validation.
        logger.error("Linting: project_manifest missing from top-level config")
        return {
            "status": "error",
            "message": "project_manifest is required at the top level of the config",
        }

    # Containment check: project_manifest is documented as a path
    # relative to the repo root, but ``os.path.join`` silently accepts
    # absolute paths and ``..`` segments that would let the subprocess
    # read outside the fetched tree. Resolve both ends and verify the
    # manifest sits under repo_root before handing it to aurig-lint.
    repo_root_resolved = Path(repo_root_path).resolve()
    manifest_resolved = Path(repo_root_path, project_manifest).resolve()
    try:
        manifest_resolved.relative_to(repo_root_resolved)
    except ValueError:
        logger.error(
            "Linting: project_manifest '%s' resolves outside repo_root (%s); "
            "refusing to proceed",
            project_manifest, repo_root_resolved,
        )
        return {
            "status": "error",
            "message": (
                f"project_manifest '{project_manifest}' resolves outside the "
                "repo root; the manifest path must be relative to the repo and "
                "contain no '..' segments"
            ),
        }
    if not manifest_resolved.is_file():
        logger.error(
            "Linting: project_manifest not found at %s", manifest_resolved
        )
        return {
            "status": "error",
            "message": f"project_manifest not found at {manifest_resolved}",
        }
    manifest_abs = str(manifest_resolved)

    # output_dir is documented as a path relative to <run_dir>. The
    # config validator enforces "relative, no '..' segments" at
    # config-load time; this is the defensive runtime backstop for
    # callers that bypass validation (e.g. ad-hoc test fixtures).
    output_dir_field = lint_config.get("output_dir", "lint_output")
    if not _is_safe_relative_path(output_dir_field):
        logger.error(
            "Linting: output_dir '%s' must be a relative path with no "
            "'..' segments",
            output_dir_field,
        )
        return {
            "status": "error",
            "message": (
                f"phases.linting.output_dir must be a relative path with "
                f"no '..' segments (got: '{output_dir_field}')"
            ),
        }
    output_dir = str(ctx.output_dir(output_dir_field))
    os.makedirs(output_dir, exist_ok=True)

    tclsh_path = lint_config.get("tclsh_path", "tclsh")
    fail_on = lint_config.get("fail_on", "error")
    fmt = lint_config.get("format", "html")
    policy = lint_config.get("policy")
    include = lint_config.get("include")
    exclude = lint_config.get("exclude")

    # ``policy`` is documented as a path relative to the fetched
    # repo root (matching project_manifest). Resolve it explicitly
    # here so the subprocess sees an absolute path and is portable
    # across whichever working directory Sentinel was launched from.
    # Absolute paths pass through unchanged so operators can point
    # at shared policy files outside the repo (e.g.
    # /etc/sentinel/team.json). Relative paths get the same
    # containment hardening as project_manifest: if the resolved
    # path lands outside repo_root (e.g. via ``..`` segments),
    # refuse to proceed — relative-path semantics means "relative
    # to the repo", and a relative input that escapes the repo is
    # ambiguous (operator typo? intentional?), so we surface a
    # targeted error and let the operator switch to an explicit
    # absolute path if that was the intent.
    if policy:
        policy_path = Path(policy)
        if policy_path.is_absolute():
            policy = str(policy_path)
        else:
            policy_resolved = Path(repo_root_path, policy).resolve()
            try:
                policy_resolved.relative_to(repo_root_resolved)
            except ValueError:
                logger.error(
                    "Linting: policy '%s' resolves outside repo_root "
                    "(%s); relative policy paths must stay under the "
                    "repo, or use an absolute path for shared policies",
                    policy, repo_root_resolved,
                )
                return {
                    "status": "error",
                    "message": (
                        f"phases.linting.policy '{policy}' resolves "
                        "outside the repo root; relative policy paths "
                        "must stay under the repo, or use an absolute "
                        "path for shared policies"
                    ),
                }
            policy = str(policy_resolved)

    cmd = [
        tclsh_path,
        runner_script,
        "-manifest", manifest_abs,
        "-fail_on", fail_on,
        "-format", fmt,
        "-outdir", output_dir,
    ]
    if policy:
        cmd.extend(["-policy", policy])
    if include:
        cmd.extend(["-include", include])
    if exclude:
        cmd.extend(["-exclude", exclude])

    logger.info("Running aurig-lint lint: %s", " ".join(cmd))

    log_dir = ctx.run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    combined_log = str(log_dir / "lint.log")

    def _safe_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _write_lint_log(stdout: str, stderr: str, summary: str = "") -> None:
        """Write the combined lint log. Called from every exit path so
        the ``log_file`` path returned in the result dict always points
        at a real file the operator can tail post-mortem.
        """
        with open(combined_log, "w", encoding="utf-8") as f:
            f.write(f"Command: {' '.join(cmd)}\n\n")
            if summary:
                f.write(f"{summary}\n\n")
            f.write("=== STDOUT ===\n")
            f.write(stdout)
            f.write("\n=== STDERR ===\n")
            f.write(stderr)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DEFAULT_LINT_TIMEOUT_SECONDS,
            # Set cwd to the fetched repo root so any latent relative-
            # path resolution inside aurig-lint (anything we don't pass
            # explicitly) uses the same base as the documented
            # contract for policy / manifest paths.
            cwd=str(repo_root_path),
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "aurig-lint lint timed out after %s seconds",
            DEFAULT_LINT_TIMEOUT_SECONDS,
        )
        _write_lint_log(
            _safe_str(getattr(exc, "stdout", "")),
            _safe_str(getattr(exc, "stderr", "")),
            summary=f"=== TIMEOUT after {DEFAULT_LINT_TIMEOUT_SECONDS}s ===",
        )
        return {
            "status": "failed",
            "error": f"Timeout after {DEFAULT_LINT_TIMEOUT_SECONDS}s",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }
    except FileNotFoundError as exc:
        logger.error(
            "Linting: tclsh executable not found on PATH (tclsh_path=%s)",
            tclsh_path,
        )
        _write_lint_log(
            stdout="",
            stderr=_safe_str(exc),
            summary=f"=== EXECUTABLE NOT FOUND: {tclsh_path} ===",
        )
        return {
            "status": "failed",
            "error": f"tclsh not found on PATH (tclsh_path={tclsh_path})",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }

    _write_lint_log(result.stdout, result.stderr)

    rc = result.returncode
    # Vendor stderr is captured in full in combined_log; the 500-char
    # tail (matching synthesis.py / OP-015) is purely for inline
    # summary readability.
    stderr_tail = result.stderr[-500:] if result.stderr else ""

    if rc == 0:
        logger.info(
            "aurig-lint lint completed cleanly (no diagnostics meet/exceed fail_on=%s)",
            fail_on,
        )
        return {
            "status": "completed",
            "exit_code": 0,
            "output_dir": output_dir,
            "log_file": combined_log,
        }
    if rc == 1:
        logger.warning(
            "aurig-lint lint reported diagnostics meeting/exceeding fail_on=%s; "
            "phase marked failed",
            fail_on,
        )
        return {
            "status": "failed",
            "exit_code": 1,
            "error": stderr_tail
            or f"lint diagnostics meet/exceed fail_on={fail_on}",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }
    if rc == 2:
        logger.error("aurig-lint lint reported a tool error (exit 2)")
        return {
            "status": "error",
            "exit_code": 2,
            "error": stderr_tail or "aurig-lint reported a tool error",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }
    # aurig-lint's documented contract is 0/1/2 only. An unexpected
    # non-zero code is either a aurig-lint bug, a tclsh crash, or a
    # future contract drift — none of which are code-quality
    # regressions. Map to ``error`` (matching the rc=2 case) so
    # operators can distinguish "broken tool / contract drift"
    # from "code has lint findings".
    logger.error("aurig-lint lint returned unexpected exit code %s", rc)
    return {
        "status": "error",
        "exit_code": rc,
        "error": f"aurig-lint returned unexpected exit code {rc}\n{stderr_tail}",
        "output_dir": output_dir,
        "log_file": combined_log,
        "full_log_file": combined_log,
    }
