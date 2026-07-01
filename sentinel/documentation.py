# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""HDL documentation phase via aurig-doc's project document runner.

Sentinel does not generate documentation itself. The documentation
phase invokes ``tools/run_doc_project_inprocess.tcl`` from the
aurig-doc repository as a subprocess (``tclsh ...``). aurig-doc owns
the HDL-aware work: walking the canonical project manifest, extracting
entity/architecture documentation, and emitting HTML/MD reports.

Distribution model (mirrors the linting phase, OP-040 v1)
---------------------------------------------------------
aurig-doc is cloned manually on the workstation that runs Sentinel and
pointed at via ``phases.documentation.aurig_doc_path`` in the YAML, or
via the env var ``SENTINEL_AURIG_DOC_PATH``. Versioned release tarballs
and single-binary packaging are tracked as future work.

Exit code contract (from aurig-doc's document runner)
-----------------------------------------------------
Documentation has no quality-threshold notion like lint's ``fail_on``;
there is no "documentation failed" state, only "docs generated" or
"the tool/setup broke". The contract is therefore simpler than lint's
0/1/2 — there is **no exit 1**:

- ``0`` — docs generated; Sentinel maps to ``completed``.
- ``2`` — any tool/setup error inside aurig-doc / tclsh / the
  subprocess. Sentinel maps to ``error`` so callers can tell a broken
  setup from a successful run.
- Any other non-zero code — treated as a tool/setup issue and mapped
  to ``error`` as well (an aurig-doc bug, a tclsh crash, or future
  contract drift). Documentation never returns ``failed``: it has no
  code-quality regression to report.

Because the phase only ever returns ``completed`` or ``error``, and
``error`` is recognised by ``main._is_failure_status``,
``global_settings.continue_on_error`` is honored automatically and
consistently with the other phases.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .run_context import RunContext


DEFAULT_DOC_TIMEOUT_SECONDS = 1800
AURIG_DOC_RUNNER_REL = ("tools", "run_doc_project_inprocess.tcl")


def _is_safe_relative_path(value: str) -> bool:
    """Return True when *value* is a relative path with at least one
    real segment and no ``..`` segments.

    Used to keep ``output_dir`` from escaping the run dir. The
    validator enforces the same check at config-load time; this is
    the defensive runtime backstop for callers that bypass
    validation (e.g. ad-hoc test fixtures). Mirrors
    ``sentinel.linting._is_safe_relative_path``: values like ``""``,
    ``"."``, ``"./"`` all normalize to ``Path(".")`` whose ``.parts``
    is the empty tuple ``()`` in pathlib, so an empty-parts check
    catches the "would resolve to run_dir itself" family of inputs.
    """
    p = Path(value)
    if p.is_absolute():
        return False
    if not p.parts:
        return False
    return ".." not in p.parts


def run_documentation(config: Dict[str, Any], ctx: RunContext) -> Optional[Dict[str, Any]]:
    """Run HDL documentation via aurig-doc's project document runner.

    Reads ``phases.documentation`` from the YAML schema and the
    top-level ``project_manifest``. Invokes
    ``tclsh <aurig-doc>/tools/run_doc_project_inprocess.tcl
    -manifest <repo>/<project_manifest> -format <html|md>
    -outdir <run_dir>/<output_dir>`` with an optional ``-verbosity``
    passed through from the YAML.

    Returns a status dict consumed by ``sentinel.main.execute_phases``.
    """
    logger = logging.getLogger(__name__)
    doc_config = (config.get("phases") or {}).get("documentation") or {}

    if not doc_config.get("enabled", False):
        logger.info("Documentation phase disabled in configuration")
        return {"status": "skipped", "message": "Phase disabled"}

    # Resolve aurig_doc_path: YAML field -> env -> targeted error.
    # An empty string in either source counts as missing.
    aurig_doc_path = doc_config.get("aurig_doc_path") or os.environ.get(
        "SENTINEL_AURIG_DOC_PATH"
    )
    if not aurig_doc_path:
        logger.error(
            "Documentation: aurig_doc_path not configured "
            "(set phases.documentation.aurig_doc_path or env "
            "SENTINEL_AURIG_DOC_PATH)"
        )
        return {
            "status": "error",
            "message": (
                "aurig_doc_path missing — set phases.documentation.aurig_doc_path "
                "in YAML or env SENTINEL_AURIG_DOC_PATH"
            ),
        }

    aurig_doc_path = os.path.expanduser(aurig_doc_path)
    if not os.path.isdir(aurig_doc_path):
        logger.error(
            "Documentation: aurig_doc_path is not a directory: %s", aurig_doc_path
        )
        return {
            "status": "error",
            "message": f"aurig_doc_path not found or not a directory: {aurig_doc_path}",
        }

    runner_script = os.path.join(aurig_doc_path, *AURIG_DOC_RUNNER_REL)
    if not os.path.isfile(runner_script):
        logger.error(
            "Documentation: aurig-doc project runner not found at %s — "
            "confirm aurig_doc_path points at the aurig-doc repository root",
            runner_script,
        )
        return {
            "status": "error",
            "message": (
                "aurig-doc document runner not found at "
                "tools/run_doc_project_inprocess.tcl under aurig_doc_path "
                f"({aurig_doc_path}); confirm the path points at the "
                "aurig-doc repository root"
            ),
        }

    # OP-034 guard, mirroring linting: distinguish "fetch ran but
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
                "Documentation cannot run: fetch phase ran but produced no "
                "usable repository (see fetch logs)"
            )
            return {
                "status": "error",
                "message": "Fetch phase ran but produced no usable repository",
            }
        logger.error(
            "Documentation cannot run: no repository available "
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
        logger.error("Documentation: project_manifest missing from top-level config")
        return {
            "status": "error",
            "message": "project_manifest is required at the top level of the config",
        }

    # Containment check (same as linting): project_manifest is
    # documented as a path relative to the repo root. Resolve both
    # ends and verify the manifest sits under repo_root before handing
    # it to aurig-doc.
    repo_root_resolved = Path(repo_root_path).resolve()
    manifest_resolved = Path(repo_root_path, project_manifest).resolve()
    try:
        manifest_resolved.relative_to(repo_root_resolved)
    except ValueError:
        logger.error(
            "Documentation: project_manifest '%s' resolves outside repo_root "
            "(%s); refusing to proceed",
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
            "Documentation: project_manifest not found at %s", manifest_resolved
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
    output_dir_field = doc_config.get("output_dir", "doc_output")
    if not _is_safe_relative_path(output_dir_field):
        logger.error(
            "Documentation: output_dir '%s' must be a relative path with no "
            "'..' segments",
            output_dir_field,
        )
        return {
            "status": "error",
            "message": (
                f"phases.documentation.output_dir must be a relative path with "
                f"no '..' segments (got: '{output_dir_field}')"
            ),
        }
    output_dir = str(ctx.output_dir(output_dir_field))
    os.makedirs(output_dir, exist_ok=True)

    tclsh_path = doc_config.get("tclsh_path", "tclsh")
    fmt = doc_config.get("format", "html")
    verbosity = doc_config.get("verbosity")

    cmd = [
        tclsh_path,
        runner_script,
        "-manifest", manifest_abs,
        "-format", fmt,
        "-outdir", output_dir,
    ]
    if verbosity is not None:
        cmd.extend(["-verbosity", str(verbosity)])

    logger.info("Running aurig-doc document: %s", " ".join(cmd))

    log_dir = ctx.run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    combined_log = str(log_dir / "documentation.log")

    def _safe_str(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _write_doc_log(stdout: str, stderr: str, summary: str = "") -> None:
        """Write the combined documentation log. Called from every exit
        path so the ``log_file`` path returned in the result dict always
        points at a real file the operator can tail post-mortem.
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
            timeout=DEFAULT_DOC_TIMEOUT_SECONDS,
            # Set cwd to the fetched repo root so any latent relative-
            # path resolution inside aurig-doc (anything we don't pass
            # explicitly) uses the same base as the documented contract
            # for the manifest path.
            cwd=str(repo_root_path),
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "aurig-doc document timed out after %s seconds",
            DEFAULT_DOC_TIMEOUT_SECONDS,
        )
        _write_doc_log(
            _safe_str(getattr(exc, "stdout", "")),
            _safe_str(getattr(exc, "stderr", "")),
            summary=f"=== TIMEOUT after {DEFAULT_DOC_TIMEOUT_SECONDS}s ===",
        )
        return {
            "status": "error",
            "error": f"Timeout after {DEFAULT_DOC_TIMEOUT_SECONDS}s",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }
    except FileNotFoundError as exc:
        logger.error(
            "Documentation: tclsh executable not found on PATH (tclsh_path=%s)",
            tclsh_path,
        )
        _write_doc_log(
            stdout="",
            stderr=_safe_str(exc),
            summary=f"=== EXECUTABLE NOT FOUND: {tclsh_path} ===",
        )
        return {
            "status": "error",
            "error": f"tclsh not found on PATH (tclsh_path={tclsh_path})",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }

    _write_doc_log(result.stdout, result.stderr)

    rc = result.returncode
    # Vendor stderr is captured in full in combined_log; the 500-char
    # tail (matching synthesis.py / linting.py) is purely for inline
    # summary readability.
    stderr_tail = result.stderr[-500:] if result.stderr else ""

    if rc == 0:
        logger.info("aurig-doc document completed (docs generated)")
        return {
            "status": "completed",
            "exit_code": 0,
            "output_dir": output_dir,
            "log_file": combined_log,
        }
    if rc == 2:
        logger.error("aurig-doc document reported a tool error (exit 2)")
        return {
            "status": "error",
            "exit_code": 2,
            "error": stderr_tail or "aurig-doc reported a tool error",
            "output_dir": output_dir,
            "log_file": combined_log,
            "full_log_file": combined_log,
        }
    # aurig-doc's documented contract is 0/2 only (no exit 1 — there is
    # no quality-threshold notion). An unexpected non-zero code is an
    # aurig-doc bug, a tclsh crash, or future contract drift — all
    # tool/setup issues, never a documentation "failure". Map to
    # ``error`` like rc=2.
    logger.error("aurig-doc document returned unexpected exit code %s", rc)
    return {
        "status": "error",
        "exit_code": rc,
        "error": f"aurig-doc returned unexpected exit code {rc}\n{stderr_tail}",
        "output_dir": output_dir,
        "log_file": combined_log,
        "full_log_file": combined_log,
    }
