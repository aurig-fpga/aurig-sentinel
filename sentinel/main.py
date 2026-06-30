# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Entry point for running Sentinel build phases."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import pwd only on Unix-like systems (Linux, macOS)
try:
    import pwd
    HAS_PWD = True
except ImportError:
    HAS_PWD = False

from .run_context import RunContext
from .config_validator import (
    ConfigValidationError,
    discover_configs,
    is_within_night_time_window,
    load_and_validate,
    load_config_file,
)
from .fetch_code import fetch_code
from .project_setup import project_setup
from .regression_testing import regression_testing_phase


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL_MAP = {
    "quiet": logging.WARNING,
    "normal": logging.INFO,
    "verbose": logging.DEBUG,
}


def init_logging(log_file: str, level_name: str = "normal") -> None:
    """Initialize logging with file and console handlers for one run."""
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    console_handler = logging.StreamHandler()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logging.root.setLevel(LOG_LEVEL_MAP.get(level_name, logging.INFO))
    logging.root.addHandler(file_handler)
    logging.root.addHandler(console_handler)

    try:
        if HAS_PWD:
            user_info = pwd.getpwuid(os.getuid())
            logging.info(
                "Logging initialized - Running as user: %s (UID: %s, GID: %s)",
                user_info.pw_name, os.getuid(), os.getgid(),
            )
        else:
            import getpass
            logging.info(
                "Logging initialized - Running as user: %s on %s",
                getpass.getuser(), platform.system(),
            )
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("Could not determine user info: %s", exc)


# ---------------------------------------------------------------------------
# Run directory and cleanup
# ---------------------------------------------------------------------------

def _project_runs_dir(config: Dict[str, Any]) -> Path:
    """Return the base directory holding all runs of a single project."""
    base_dir = (config.get("output") or {}).get("base_dir", "./runs")
    project_name = config["project"]["name"]
    return Path(base_dir).expanduser().resolve() / project_name


def cleanup_old_runs(project_runs_dir: Path, retention_days: int = 30) -> None:
    """Delete run subdirectories older than *retention_days* for one project.

    Skips directories that contain a ``.release`` or ``.keep`` marker. The
    project_runs_dir itself is left in place even if every child is removed.
    """
    if not project_runs_dir.is_dir():
        return

    now = time.time()
    cutoff_seconds = retention_days * 86400

    for entry in project_runs_dir.iterdir():
        if not entry.is_dir():
            continue
        if (entry / ".release").exists() or (entry / ".keep").exists():
            continue
        try:
            age_days = (now - entry.stat().st_mtime) / 86400
            if (now - entry.stat().st_mtime) > cutoff_seconds:
                logging.info(
                    "Cleaning up old run folder: %s (age: %.1f days)", entry, age_days
                )
                shutil.rmtree(entry)
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Could not remove folder %s: %s", entry, exc)


def package_artifacts(run_dir: Path, artifact_paths: List[str]) -> Optional[str]:
    """Collect output folders into ``<run_dir>/artifacts/`` and zip them."""
    artifact_dir = run_dir / "artifacts"
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for path in artifact_paths:
        if not path:
            continue
        source = Path(path).resolve()
        if not source.exists():
            continue
        destination = artifact_dir / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        copied += 1

    if not copied:
        logging.info("No artifacts were collected for bundling")
        return None

    archive_path = shutil.make_archive(
        str(run_dir / "sentinel_artifacts"), "zip", artifact_dir
    )
    logging.info("Artifacts archived to %s", archive_path)
    return archive_path


# ---------------------------------------------------------------------------
# Phase execution
# ---------------------------------------------------------------------------

def _phase_enabled(config: Dict[str, Any], name: str) -> bool:
    return bool(((config.get("phases") or {}).get(name) or {}).get("enabled", False))


_FAILURE_STATUSES = frozenset({"failed", "error"})


def _is_failure_status(status: Any) -> bool:
    """Return True when ``status`` represents a terminal phase failure.

    Phase modules return a mixed bag of failure markers — uppercase
    ``"FAILED"`` from this module's own ``except Exception`` handlers,
    lowercase ``"failed"`` from in-phase mid-execution failure paths,
    and lowercase ``"error"`` from in-phase configuration/validation
    failures. The end-of-run aggregation must recognise all three so a
    structured early-return doesn't silently get reported as success
    (closes OP-035).
    """
    if not isinstance(status, str):
        return False
    return status.lower() in _FAILURE_STATUSES


def _abort_if_failed(
    phase_name: str,
    status: Any,
    continue_on_error: bool,
    detail: str = "",
) -> None:
    """Raise ``RuntimeError`` when a phase returned a failure status
    and ``continue_on_error`` is disabled.

    Phase functions historically signalled failure two ways: by
    raising (caught by the per-phase ``except Exception`` block) or by
    returning ``{"status": "error" | "failed" | "FAILED", ...}``. The
    exception path already honors ``continue_on_error``; the return
    path did not, which meant a documented "abort on first failure"
    run could still launch ``synthesis`` after a regression early
    return like "No repository available". This helper closes that
    gap so the abort semantics are the same regardless of how the
    phase signalled the failure.
    """
    if not continue_on_error and _is_failure_status(status):
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{phase_name} phase failed{suffix}")


def execute_phases(config: Dict[str, Any], config_path: str) -> Dict[str, Any]:
    """Run all enabled phases for a single validated config.

    Returns a dict ``{"status": ok|failed, "failed_phases": [...]}``.
    The caller is responsible for night-window gating and aggregation.
    """
    project_name = config["project"]["name"]
    project_runs = _project_runs_dir(config)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = project_runs / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level = (config.get("global_settings") or {}).get("log_level", "normal")
    init_logging(str(log_dir / "sentinel.log"), level_name=log_level)

    ctx = RunContext.for_run(run_dir)

    artifact_paths: List[str] = [str(log_dir)]
    phase_results: Dict[str, Dict[str, Any]] = {}
    continue_on_error = (config.get("global_settings") or {}).get("continue_on_error", True)

    start_time = datetime.now()
    logging.info(
        "Starting project '%s' from config '%s' at %s",
        project_name, config_path, start_time,
    )

    bundle_zip = (config.get("output") or {}).get("bundle_zip", True)

    try:
        # Fetch is implicit-required; respect skip semantics if user disables it.
        fetch_enabled = (config.get("fetch") or {}).get("enabled", True)
        if fetch_enabled:
            try:
                fetch_result = fetch_code(config, ctx)
                phase_results["fetch"] = {"status": "OK" if fetch_result else "FAILED"}
                if not fetch_result and not continue_on_error:
                    raise RuntimeError("Fetch phase failed")
            except Exception as exc:
                phase_results["fetch"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["fetch"] = {"status": "SKIPPED"}

        # pre_run / project_setup
        pre_run_cfg = config.get("pre_run") or {}
        if pre_run_cfg.get("enabled", False):
            try:
                project_setup(config, ctx)
                phase_results["pre_run"] = {"status": "OK"}
            except Exception as exc:
                phase_results["pre_run"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["pre_run"] = {"status": "SKIPPED"}

        # linting (OP-040): wired to aurig-lint's project lint runner via subprocess.
        if _phase_enabled(config, "linting"):
            try:
                from .linting import run_linting  # lazy
                linting_result = run_linting(config, ctx)
                if linting_result:
                    # Propagate diagnostic context (error / message /
                    # log_file / output_dir) into phase_results so the
                    # end-of-run summary loop and downstream tooling
                    # can surface "why" alongside "what". Without this,
                    # under continue_on_error=True the operator sees
                    # "linting: failed" with no detail. Status is
                    # always included; the other keys only when the
                    # phase returned them.
                    entry: Dict[str, Any] = {
                        "status": linting_result.get("status", "OK"),
                    }
                    for key in ("error", "message", "log_file",
                                "full_log_file", "output_dir", "exit_code"):
                        value = linting_result.get(key)
                        if value is not None:
                            entry[key] = value
                    phase_results["linting"] = entry
                    if linting_result.get("output_dir"):
                        artifact_paths.append(linting_result["output_dir"])
                    _abort_if_failed(
                        "linting",
                        entry["status"],
                        continue_on_error,
                        detail=(linting_result.get("message")
                                or linting_result.get("error") or ""),
                    )
                else:
                    phase_results["linting"] = {"status": "SKIPPED"}
            except Exception as exc:
                phase_results["linting"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["linting"] = {"status": "SKIPPED"}

        # documentation: wired to aurig-doc's project document runner via
        # subprocess. Mirrors the linting block above — the only contract
        # difference is the exit-code mapping (doc has no exit 1 / no
        # quality-failure notion; see sentinel.documentation).
        if _phase_enabled(config, "documentation"):
            try:
                from .documentation import run_documentation  # lazy
                doc_result = run_documentation(config, ctx)
                if doc_result:
                    # Propagate diagnostic context into phase_results so
                    # the end-of-run summary loop and downstream tooling
                    # can surface "why" alongside "what" — same as linting.
                    entry: Dict[str, Any] = {
                        "status": doc_result.get("status", "OK"),
                    }
                    for key in ("error", "message", "log_file",
                                "full_log_file", "output_dir", "exit_code"):
                        value = doc_result.get(key)
                        if value is not None:
                            entry[key] = value
                    phase_results["documentation"] = entry
                    if doc_result.get("output_dir"):
                        artifact_paths.append(doc_result["output_dir"])
                    _abort_if_failed(
                        "documentation",
                        entry["status"],
                        continue_on_error,
                        detail=(doc_result.get("message")
                                or doc_result.get("error") or ""),
                    )
                else:
                    phase_results["documentation"] = {"status": "SKIPPED"}
            except Exception as exc:
                phase_results["documentation"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["documentation"] = {"status": "SKIPPED"}

        # regression
        if _phase_enabled(config, "regression"):
            try:
                regression_result = regression_testing_phase(config, ctx)
                if regression_result:
                    phase_results["regression"] = {
                        "status": regression_result.get("status", "OK")
                    }
                    if regression_result.get("output_dir"):
                        artifact_paths.append(regression_result["output_dir"])
                    _abort_if_failed(
                        "regression",
                        phase_results["regression"]["status"],
                        continue_on_error,
                        detail=(regression_result.get("message")
                                or regression_result.get("error") or ""),
                    )
                else:
                    phase_results["regression"] = {"status": "SKIPPED"}
            except Exception as exc:
                phase_results["regression"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["regression"] = {"status": "SKIPPED"}

        # synthesis
        if _phase_enabled(config, "synthesis"):
            try:
                from .synthesis import run_synthesis  # lazy
                synthesis_result = run_synthesis(config, ctx)
                if synthesis_result:
                    phase_results["synthesis"] = {
                        "status": synthesis_result.get("status", "OK")
                    }
                    if synthesis_result.get("output_dir"):
                        artifact_paths.append(synthesis_result["output_dir"])
                    _abort_if_failed(
                        "synthesis",
                        phase_results["synthesis"]["status"],
                        continue_on_error,
                        detail=(synthesis_result.get("message")
                                or synthesis_result.get("error") or ""),
                    )
                else:
                    phase_results["synthesis"] = {"status": "SKIPPED"}
            except Exception as exc:
                phase_results["synthesis"] = {"status": "FAILED", "error": str(exc)}
                if not continue_on_error:
                    raise
        else:
            phase_results["synthesis"] = {"status": "SKIPPED"}

        # deployment placeholder (roadmap)
        if _phase_enabled(config, "deployment"):
            logging.info(
                "Phase 'deployment' enabled but not implemented yet (roadmap)"
            )
            phase_results["deployment"] = {"status": "PENDING", "note": "not implemented"}
        else:
            phase_results["deployment"] = {"status": "SKIPPED"}

        duration = int((datetime.now() - start_time).total_seconds())

        logging.info("=" * 60)
        logging.info("PHASE SUMMARY")
        logging.info("=" * 60)
        for phase_name, result in phase_results.items():
            status = result.get("status", "UNKNOWN")
            error = result.get("error")
            if error:
                logging.info("%s: %s - %s", phase_name, status, error)
            else:
                logging.info("%s: %s", phase_name, status)
        logging.info("=" * 60)

        failed_phases = [
            n for n, r in phase_results.items()
            if _is_failure_status(r.get("status"))
        ]
        if failed_phases:
            logging.warning(
                "Project '%s' completed with failures in %s seconds (failed: %s)",
                project_name, duration, ", ".join(failed_phases),
            )
            return {"status": "failed", "failed_phases": failed_phases, "results": phase_results}
        logging.info(
            "SUCCESS - Project '%s' completed in %s seconds.", project_name, duration
        )
        return {"status": "ok", "failed_phases": [], "results": phase_results}

    finally:
        if bundle_zip:
            package_artifacts(run_dir, artifact_paths)


# ---------------------------------------------------------------------------
# Per-config pipeline
# ---------------------------------------------------------------------------

def _process_config(path: str, dry_run: bool) -> Tuple[str, str]:
    """Process a single config file.

    Returns (state, detail) where state is one of:
        "ok"       phases ran (or dry-run validated) successfully
        "skipped"  validation failed, runner moves on
        "failed"   validation passed but phase execution failed
        "blocked"  outside the configured night-time window
    """
    try:
        config = load_and_validate(path)
    except ConfigValidationError as exc:
        kind = "load error" if exc.is_hard else "validation"
        detail = f"{kind}: {'; '.join(exc.errors)}"
        logging.error("Skipping %s — %s", path, detail)
        return ("skipped", detail)

    if not is_within_night_time_window(config):
        msg = "outside configured night_time_window"
        logging.warning("Skipping %s — %s", path, msg)
        return ("blocked", msg)

    project_name = config["project"]["name"]

    if dry_run:
        enabled = [
            n for n, spec in (config.get("phases") or {}).items()
            if isinstance(spec, dict) and spec.get("enabled")
        ]
        msg = f"would run project '{project_name}' (phases: {', '.join(enabled) or 'none'})"
        print(f"[dry-run] {path}: {msg}")
        return ("ok", "dry-run")

    try:
        result = execute_phases(config, path)
    except Exception as exc:
        logging.error("Execution failed for %s: %s", path, exc)
        return ("failed", str(exc))

    cleanup_cfg = (config.get("global_settings") or {}).get("cleanup") or {}
    if cleanup_cfg.get("enabled", False):
        retention = cleanup_cfg.get("retention_days", 30)
        cleanup_old_runs(_project_runs_dir(config), retention_days=retention)

    if result["status"] == "failed":
        return ("failed", f"failed phases: {', '.join(result['failed_phases'])}")
    return ("ok", "completed")


# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------

def _resolve_config_dir() -> Optional[Path]:
    """Return the first existing config directory in priority order, or None."""
    env_dir = os.environ.get("SENTINEL_CONFIGS_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser()
        if candidate.is_dir():
            return candidate
    cwd_dir = Path.cwd() / "configs"
    if cwd_dir.is_dir():
        return cwd_dir
    home_dir = Path.home() / ".sentinel" / "configs"
    if home_dir.is_dir():
        return home_dir
    return None


def _check_project_name_collisions(
    paths: List[str],
) -> Tuple[Dict[str, List[str]], List[Tuple[str, str]]]:
    """Detect duplicate ``project.name`` declarations across configs.

    Returns ``(collisions, load_failures)``:

    - ``collisions`` is ``{project_name: [paths...]}`` for every name
      claimed by more than one config (case-insensitive match — see
      below). Empty when there are no duplicates.
    - ``load_failures`` is ``[(path, error_message), ...]`` for every
      config that could not be opened, decoded, or parsed during the
      pre-scan. The caller decides what to do with it: when the
      runner aborts on a collision, ``main`` surfaces this list so
      operators see the unreadable files (otherwise they'd be silent
      because the per-config loop never runs); when there are no
      collisions, the per-config loop in ``_process_config`` will
      report each one as a normal "skipped (load error: ...)" entry.

    The uniqueness constraint is global: ``project.name`` must be
    unique across the discovered configs regardless of
    ``output.base_dir``. Two motivations:

    1. **Output collision** — when both configs use the same
       ``base_dir`` (the common case, since ``output.base_dir``
       defaults to ``./runs``) they write into the same
       ``<base_dir>/<project.name>/`` directory; a second run inside
       the same wall-clock second can stomp on the first's run folder.
    2. **Identity ambiguity** — even when ``base_dir`` differs, every
       log line and every entry in the per-run aggregate summary
       refers to projects only by name (``"Project 'alpha' completed
       in N seconds"``). Two configs claiming the same name leave the
       operator unable to tell their runs apart in monitoring.

    Names are matched case-insensitively (via :py:meth:`str.casefold`)
    so ``alpha`` and ``ALPHA`` collide on every OS. This catches the
    Windows-only filesystem case (NTFS resolves them to the same
    folder) and also keeps the identity-ambiguity rationale honest on
    POSIX, where two near-identical names would still confuse logs and
    summaries. The error output preserves the operator-declared
    capitalisation so they can spot which file to fix.

    Configs that load successfully but don't expose ``project`` as a
    mapping or don't declare a usable ``project.name`` are skipped
    without accumulating into ``load_failures`` — those are schema
    problems that
    :func:`sentinel.config_validator.validate_config` will reject
    later in ``_process_config``. ``load_failures`` only records the
    hard load-time failures
    :func:`sentinel.config_validator.load_config_file` raises:
    missing file, unopenable, non-UTF-8, unparsable YAML, empty
    file, or non-mapping YAML root.
    """
    # key = casefolded name; value = (display_name, [paths]).
    # display_name is the first capitalisation we saw — we report it
    # back to the operator verbatim instead of the casefolded form.
    by_key: Dict[str, Tuple[str, List[str]]] = {}
    load_failures: List[Tuple[str, str]] = []
    for path in paths:
        try:
            data = load_config_file(path)
        except ConfigValidationError as exc:
            load_failures.append((path, "; ".join(exc.errors)))
            continue
        project = data.get("project")
        if not isinstance(project, dict):
            # E.g. a YAML with `project: alpha` (scalar) instead of a
            # mapping. Validation will reject it later; here we just
            # skip so the collision scan can still process its peers.
            continue
        name = project.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        stripped = name.strip()
        key = stripped.casefold()
        if key in by_key:
            by_key[key][1].append(path)
        else:
            by_key[key] = (stripped, [path])
    collisions = {
        display: ps for display, ps in by_key.values() if len(ps) > 1
    }
    return collisions, load_failures


class _RunnerConfigError(Exception):
    """Misconfiguration discovered before the per-config loop runs.

    Carries the message to print on stderr and the exit code ``main``
    should return. Lets ``_list_configs`` signal "abort with code N"
    without calling ``sys.exit`` itself, so for every error path
    Sentinel itself owns, ``main`` stays a normal function and
    returns an ``int``.

    One pre-existing exception remains: argparse's
    :py:meth:`ArgumentParser.error` (used here for the ``--config`` /
    ``--config-dir`` mutex check and for any malformed CLI input)
    raises ``SystemExit(2)`` directly from inside ``main``. That is
    argparse's own convention and we don't override it; callers that
    want to invoke ``main`` programmatically without crashing on bad
    CLI arguments should pre-validate their ``argv`` or wrap the call
    in ``try/except SystemExit``.

    The ``__main__`` shim wraps the normal return value in
    ``SystemExit`` so the process exit code reflects the runner's
    outcome.
    """

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _list_configs(args: argparse.Namespace) -> List[str]:
    """Resolve the list of config files to process based on CLI arguments.

    Raises ``_RunnerConfigError`` (with exit code 2) for any input
    problem that prevents discovery: a ``--config`` path that doesn't
    point at a file, a ``--config-dir`` that isn't a directory, or no
    default config directory found via the env var / cwd / home
    fallback chain.
    """
    if args.config:
        path = Path(args.config).expanduser()
        # Path.is_file() calls stat(); stat can raise OSError on a
        # permission-denied parent dir, a broken symlink, or a network
        # FS hiccup. Catch and convert so we return a clean exit code
        # 2 instead of crashing main with a traceback.
        try:
            exists_as_file = path.is_file()
        except OSError as exc:
            detail = exc.strerror or "OS error"
            raise _RunnerConfigError(
                f"error: could not access config file {args.config}: {detail}"
            ) from exc
        if exists_as_file:
            return [str(path.resolve())]
        # Distinguish "doesn't exist" from "exists but isn't a file"
        # so the operator gets a helpful message. is_dir() can raise
        # the same OSError family — treat that as "not a directory".
        try:
            is_existing_dir = path.is_dir()
        except OSError:
            is_existing_dir = False
        if is_existing_dir:
            raise _RunnerConfigError(
                f"error: --config expects a YAML file but {args.config} "
                f"is a directory (use --config-dir to process a directory "
                f"of configs)."
            )
        raise _RunnerConfigError(f"error: config file not found: {args.config}")

    if args.config_dir:
        directory = Path(args.config_dir).expanduser()
        # Same OSError concern as the --config branch above.
        try:
            exists_as_dir = directory.is_dir()
        except OSError as exc:
            detail = exc.strerror or "OS error"
            raise _RunnerConfigError(
                f"error: could not access config directory {args.config_dir}: {detail}"
            ) from exc
        if not exists_as_dir:
            raise _RunnerConfigError(
                f"error: config directory not found: {args.config_dir}"
            )
        return discover_configs(str(directory))

    directory = _resolve_config_dir()
    if directory is None:
        raise _RunnerConfigError(
            "error: No config directory found. "
            "Set SENTINEL_CONFIGS_DIR, create ./configs/ or ~/.sentinel/configs/, "
            "or pass --config / --config-dir explicitly."
        )
    return discover_configs(str(directory))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Sentinel FPGA build runner (YAML schema v1.0).",
    )
    parser.add_argument(
        "-c", "--config",
        help="Path to a single YAML config file.",
    )
    parser.add_argument(
        "--config-dir",
        help="Directory containing YAML configs. Top-level *.yaml only; "
             "files under disabled/ are ignored.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate every discovered config and print what would run, "
             "without executing any phase.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.config and args.config_dir:
        parser.error("--config and --config-dir are mutually exclusive")

    try:
        configs = _list_configs(args)
    except _RunnerConfigError as exc:
        print(exc.message, file=sys.stderr)
        return exc.code
    if not configs:
        print(
            "warning: no active config files found "
            "(an empty configs directory yields exit 2 so cron / Task "
            "Scheduler can detect a missed nightly run).",
            file=sys.stderr,
        )
        return 2

    collisions, load_failures = _check_project_name_collisions(configs)
    if collisions:
        print(
            "error: project.name must be unique across the discovered "
            "configs (collisions stomp on each other's run folders when "
            "output.base_dir matches, and ambiguate logs/summaries even "
            "when it does not).",
            file=sys.stderr,
        )
        for name, paths in sorted(collisions.items()):
            print(f"  '{name}' declared by:", file=sys.stderr)
            for p in paths:
                print(f"    - {p}", file=sys.stderr)
        if load_failures:
            # The collision exit short-circuits the per-config loop, so
            # configs that failed to load would otherwise vanish from
            # the report. Surface them here so the operator sees every
            # file that needs attention, not just the colliding ones.
            print(
                "note: the following configs could not be checked for "
                "collisions and would also need attention:",
                file=sys.stderr,
            )
            for fail_path, fail_err in load_failures:
                # MarkedYAMLError messages span multiple lines (mark
                # offset + snippet + caret). Indent continuations to
                # align with the bullet body so the "note:" section
                # stays parseable.
                indented = fail_err.replace("\n", "\n      ")
                print(f"  - {fail_path}: {indented}", file=sys.stderr)
        return 2

    counts = {"ok": 0, "skipped": 0, "failed": 0, "blocked": 0}
    failures: List[Tuple[str, str, str]] = []  # (path, state, detail)

    for path in configs:
        state, detail = _process_config(path, dry_run=args.dry_run)
        counts[state] += 1
        if state != "ok":
            failures.append((path, state, detail))

    total = len(configs)
    print(
        f"\n{total} config(s) processed: "
        f"{counts['ok']} ok, "
        f"{counts['skipped']} skipped (validation), "
        f"{counts['blocked']} blocked (night-window), "
        f"{counts['failed']} failed (execution)."
    )
    if failures:
        print("Details:")
        for path, state, detail in failures:
            print(f"  [{state}] {path}: {detail}")

    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
