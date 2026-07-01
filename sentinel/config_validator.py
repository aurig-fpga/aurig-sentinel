# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Configuration loading and validation for Sentinel YAML configs.

Validation-vs-runtime contract
------------------------------
The validator only enforces *format* rules: required keys, types,
allowed enum values, and structural shape. It deliberately does not
inspect the host filesystem, so a config that targets paths only
present on the production host (``fetch.local_path``,
``phases.synthesis.synthesis_tool_path``, vendor install dirs, etc.)
still passes ``--dry-run`` on a developer laptop, in CI, or under a
Copilot lint pass.

Existence and reachability of those paths is verified by the phase
modules at run time (``fetch_code``, ``synthesis._run_vivado_*``, ...)
where a missing path becomes a phase failure that respects
``global_settings.continue_on_error`` rather than a hard validator
rejection. New validator code should follow the same split: format
goes here, anything that touches the live filesystem belongs in the
phase module.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml


SUPPORTED_SCHEMA_VERSIONS = {"1.0"}
VALID_LOG_LEVELS = {"quiet", "normal", "verbose"}
VALID_FETCH_TYPES = {"git", "local"}
KNOWN_PHASES = ("linting", "documentation", "regression", "synthesis", "deployment")
VALID_LINTING_FAIL_ON = {"error", "warning", "info", "any", "none"}
VALID_LINTING_FORMATS = {"html", "md", "csv", "text"}
# aurig-doc emits only HTML/MD — no csv/text like lint.
VALID_DOCUMENTATION_FORMATS = {"html", "md"}


class ConfigValidationError(Exception):
    """Raised for any configuration problem.

    Attributes:
        errors: List of human-readable error messages, each pointing to the
            offending dotted path (e.g. "phases.synthesis.enabled").
        is_hard: True if the config could not be loaded at all (missing file
            or unparsable YAML); False for schema/value problems on a parsed
            document. The runner uses this to decide how to report the
            failure when iterating over multiple configs.
    """

    def __init__(self, errors: List[str], is_hard: bool = False):
        self.errors = list(errors)
        self.is_hard = is_hard
        prefix = "Configuration could not be loaded" if is_hard else "Configuration validation failed"
        body = "\n".join(f"  - {e}" for e in self.errors)
        super().__init__(f"{prefix}:\n{body}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_config_file(path: str) -> Dict[str, Any]:
    """Read a YAML config file from disk.

    Raises ConfigValidationError(is_hard=True) if the file is missing,
    cannot be opened, contains non-UTF-8 bytes, is not parsable as
    YAML, is empty, or parses to a non-mapping YAML root. Returns the
    parsed mapping otherwise. Validation against the schema (required
    fields, allowed values, etc.) is a separate step
    (:func:`validate_config`).

    The wrap is exhaustive on purpose. Callers handle
    ``ConfigValidationError`` differently:

    - ``main._process_config`` records each failure as a "skipped
      (load error: ...)" entry in the aggregate summary, so the
      operator sees every unreadable file at the end of the run.
    - ``main._check_project_name_collisions`` buffers the failures
      into a separate ``load_failures`` list returned alongside the
      collision map. ``main`` surfaces that list explicitly when a
      collision causes an early exit; otherwise the per-config loop
      runs and ``_process_config`` reports them.

    Either way, a raw ``OSError`` or ``UnicodeDecodeError`` would
    crash the whole multi-config run on a single unreadable file —
    hence the wrap.

    Error messages do **not** embed the file path: callers always
    have ``path`` in scope and prefix it themselves when surfacing
    the failure (otherwise the path appears twice in user output).
    """
    p = Path(path)
    # Step 1: read the file as text. Errors raised here are all
    # filesystem / decoding issues — we don't want yaml in the picture
    # yet because passing a file object to yaml.safe_load lets PyYAML
    # embed the stream name (= the path) into MarkedYAMLError messages
    # via the parse marks, defeating the path-free contract.
    try:
        with p.open("r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError as exc:
        raise ConfigValidationError(["Config file not found"], is_hard=True) from exc
    except IsADirectoryError as exc:
        raise ConfigValidationError(
            ["Config path is a directory, not a file"], is_hard=True,
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigValidationError(
            [f"Config file is not valid UTF-8: {exc}"],
            is_hard=True,
        ) from exc
    except OSError as exc:
        # Permission denied, transient network-filesystem error, the
        # parent dir becoming unreachable, etc. Catching OSError here
        # also subsumes the (now-removed) `p.is_file()` pre-check,
        # whose stat() would have raised the same family of errors
        # outside any try/except and crashed the whole runner.
        # Build the message from exc.strerror rather than str(exc):
        # the latter renders as `[Errno N] strerror: 'path'` and would
        # reintroduce the path duplication the caller-prefixes-once
        # contract just removed.
        detail = exc.strerror or (
            f"OS error {exc.errno}" if exc.errno is not None else "OS error"
        )
        raise ConfigValidationError(
            [f"Could not read config file: {detail}"],
            is_hard=True,
        ) from exc

    # Step 2: parse the text. yaml.safe_load on a str defaults the
    # mark name to "<unicode string>", so str(exc) on a parse error
    # carries the offset/snippet but never the file path.
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            [f"YAML parse error: {exc}"], is_hard=True,
        ) from exc
    if data is None:
        raise ConfigValidationError(["Config file is empty"], is_hard=True)
    if not isinstance(data, dict):
        raise ConfigValidationError(
            [f"Config root must be a mapping, got {type(data).__name__}"],
            is_hard=True,
        )
    return data


def load_and_validate(path: str) -> Dict[str, Any]:
    """Convenience: load_config_file + validate_config."""
    data = load_config_file(path)
    validate_config(data)
    return data


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _validate_time_format(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def _validate_schema_version(config: Dict[str, Any], errors: List[str]) -> None:
    version = config.get("schema_version")
    if version is None:
        errors.append("schema_version is required")
        return
    if not isinstance(version, str) or version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            f"schema_version: unsupported version {version!r}, expected 1.0"
        )


def _validate_project(config: Dict[str, Any], errors: List[str]) -> None:
    project = config.get("project")
    if project is None:
        errors.append("project is required")
        return
    if not isinstance(project, dict):
        errors.append("project must be a mapping")
        return
    if not _is_str(project.get("name")):
        errors.append("project.name must be a non-empty string")
    desc = project.get("description")
    if desc is not None and not isinstance(desc, str):
        errors.append("project.description must be a string when present")


def _validate_global_settings(config: Dict[str, Any], errors: List[str]) -> None:
    gs = config.get("global_settings", {})
    if not isinstance(gs, dict):
        errors.append("global_settings must be a mapping")
        return

    window = gs.get("night_time_window")
    if window is not None:
        if not isinstance(window, dict):
            errors.append("global_settings.night_time_window must be a mapping")
        else:
            for key in ("start", "end"):
                if key not in window:
                    errors.append(f"global_settings.night_time_window.{key} is required")
                elif not _validate_time_format(window[key]):
                    errors.append(
                        f"global_settings.night_time_window.{key} must be in HH:MM format"
                    )

    cleanup = gs.get("cleanup")
    if cleanup is not None:
        if not isinstance(cleanup, dict):
            errors.append("global_settings.cleanup must be a mapping")
        else:
            enabled = cleanup.get("enabled", False)
            if not isinstance(enabled, bool):
                errors.append("global_settings.cleanup.enabled must be a boolean")
            retention = cleanup.get("retention_days", 30)
            if not isinstance(retention, int) or isinstance(retention, bool) or retention <= 0:
                errors.append("global_settings.cleanup.retention_days must be a positive integer")

    coe = gs.get("continue_on_error", True)
    if not isinstance(coe, bool):
        errors.append("global_settings.continue_on_error must be a boolean")

    log_level = gs.get("log_level", "normal")
    if log_level not in VALID_LOG_LEVELS:
        errors.append(
            f"global_settings.log_level must be one of: {', '.join(sorted(VALID_LOG_LEVELS))}"
        )


def _validate_fetch(config: Dict[str, Any], errors: List[str]) -> None:
    fetch = config.get("fetch")
    if fetch is None:
        errors.append("fetch is required")
        return
    if not isinstance(fetch, dict):
        errors.append("fetch must be a mapping")
        return

    fetch_type = fetch.get("type")
    if fetch_type not in VALID_FETCH_TYPES:
        errors.append(
            f"fetch.type must be one of: {', '.join(sorted(VALID_FETCH_TYPES))}"
        )
        return

    if fetch_type == "git":
        if not _is_str(fetch.get("url")):
            errors.append("fetch.url must be a non-empty string when fetch.type is 'git'")
        branch = fetch.get("branch", "main")
        if not _is_str(branch):
            errors.append("fetch.branch must be a non-empty string")
        shallow = fetch.get("shallow_clone", True)
        if not isinstance(shallow, bool):
            errors.append("fetch.shallow_clone must be a boolean")
        depth = fetch.get("depth", 1)
        if not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0:
            errors.append("fetch.depth must be a positive integer")
        ssh_key = fetch.get("ssh_key_path")
        if ssh_key is not None and not _is_str(ssh_key):
            errors.append("fetch.ssh_key_path must be a non-empty string when present")

    elif fetch_type == "local":
        local_path = fetch.get("local_path")
        if not _is_str(local_path):
            errors.append("fetch.local_path must be a non-empty string when fetch.type is 'local'")
        # No existence check here on purpose: see the module-level
        # "validation-vs-runtime" contract note. fetch_code.fetch_code
        # is responsible for surfacing a missing local_path at run
        # time as a phase failure.


def _validate_project_manifest(config: Dict[str, Any], errors: List[str]) -> None:
    manifest = config.get("project_manifest")
    if manifest is None:
        errors.append("project_manifest is required")
    elif not _is_str(manifest):
        errors.append("project_manifest must be a non-empty string")


def _validate_pre_run(config: Dict[str, Any], errors: List[str]) -> None:
    pre = config.get("pre_run")
    if pre is None:
        return
    if not isinstance(pre, dict):
        errors.append("pre_run must be a mapping")
        return

    enabled = pre.get("enabled", False)
    if not isinstance(enabled, bool):
        errors.append("pre_run.enabled must be a boolean")

    scripts = pre.get("scripts", [])
    scripts_ok = isinstance(scripts, list) and all(_is_str(s) for s in scripts)
    if not scripts_ok:
        errors.append("pre_run.scripts must be a list of non-empty strings")

    program = pre.get("program")
    if program is not None and not _is_str(program):
        errors.append("pre_run.program must be a non-empty string when present")

    args = pre.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        errors.append("pre_run.args must be a list of strings")

    # Validate only if the key is present. The runtime default lives
    # in sentinel.project_setup.DEFAULT_PRE_RUN_TIMEOUT_SECONDS — keep
    # it single-sourced there rather than duplicating a literal here
    # that could silently drift.
    if "timeout_seconds" in pre:
        timeout = pre["timeout_seconds"]
        if (not isinstance(timeout, int)) or isinstance(timeout, bool) or timeout <= 0:
            errors.append("pre_run.timeout_seconds must be a positive integer")

    # OP-023: enabled-but-empty silent no-op trap. If the operator
    # turns the phase on but provides neither scripts nor program,
    # project_setup would report the phase as completed while
    # contributing nothing — looks like work happened. Reject the
    # config instead.
    has_scripts = scripts_ok and any(_is_str(s) for s in scripts)
    has_program = _is_str(program) if program is not None else False
    if enabled is True and not has_scripts and not has_program:
        errors.append(
            "pre_run.enabled is true but neither pre_run.scripts "
            "(non-empty) nor pre_run.program is configured — disable "
            "the phase or supply at least one of the two"
        )


def _validate_phases(config: Dict[str, Any], errors: List[str]) -> None:
    phases = config.get("phases")
    if phases is None:
        errors.append("phases is required")
        return
    if not isinstance(phases, dict):
        errors.append("phases must be a mapping")
        return

    enabled_count = 0
    for name, spec in phases.items():
        path = f"phases.{name}"
        if name not in KNOWN_PHASES:
            errors.append(f"{path}: unknown phase (known: {', '.join(KNOWN_PHASES)})")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{path} must be a mapping")
            continue
        enabled = spec.get("enabled", False)
        if not isinstance(enabled, bool):
            errors.append(f"{path}.enabled must be a boolean")
            continue
        if enabled:
            enabled_count += 1

    if enabled_count == 0:
        errors.append("phases: at least one phase must be enabled")


def _validate_linting(config: Dict[str, Any], errors: List[str]) -> None:
    """Validate the phases.linting subblock (format-only).

    The values that touch the host filesystem (``aurig_lint_path``,
    ``tclsh_path``, ``policy``) are not checked here — existence is
    verified by :mod:`sentinel.linting` at run time, per the
    module-level validation-vs-runtime contract. Any field that is
    present is validated; absent fields fall back to their runtime
    defaults inside :mod:`sentinel.linting`.
    """
    lint = (config.get("phases") or {}).get("linting")
    if not isinstance(lint, dict):
        return

    aurig_lint_path = lint.get("aurig_lint_path")
    if aurig_lint_path is not None and not _is_str(aurig_lint_path):
        errors.append(
            "phases.linting.aurig_lint_path must be a non-empty string when present"
        )

    tclsh_path = lint.get("tclsh_path")
    if tclsh_path is not None and not _is_str(tclsh_path):
        errors.append(
            "phases.linting.tclsh_path must be a non-empty string when present"
        )

    fail_on = lint.get("fail_on")
    if fail_on is not None and fail_on not in VALID_LINTING_FAIL_ON:
        errors.append(
            "phases.linting.fail_on must be one of: "
            f"{', '.join(sorted(VALID_LINTING_FAIL_ON))}"
        )

    fmt = lint.get("format")
    if fmt is not None and fmt not in VALID_LINTING_FORMATS:
        errors.append(
            "phases.linting.format must be one of: "
            f"{', '.join(sorted(VALID_LINTING_FORMATS))}"
        )

    output_dir = lint.get("output_dir")
    if output_dir is not None:
        if not _is_str(output_dir):
            errors.append(
                "phases.linting.output_dir must be a non-empty string when present"
            )
        else:
            # output_dir is resolved under <run_dir>/ at runtime; an
            # absolute path, a ``..`` segment, or a value that
            # normalizes to ``.`` would let the phase write outside
            # the run directory (or *as* the run directory itself —
            # in the ``.`` case ``package_artifacts`` would later
            # ``copytree`` the run dir into a subdir of itself and
            # break the bundle step). Catch that at config-load time
            # rather than letting it surface as a surprising
            # filesystem effect at 03:00.
            p = Path(output_dir)
            # ``not p.parts`` catches the ``.`` / ``./`` / ``""`` /
            # ``./.`` family that all normalize to ``Path(".")``: in
            # pathlib ``Path('.').parts == ()`` (verified empirically
            # on Python 3.8+ — the tuple is empty, not ``('.',)``),
            # so an empty-parts check is the correct test. See
            # ``sentinel.linting._is_safe_relative_path`` for the
            # runtime mirror of this rule and
            # ``TestLintingSchemaValidation.test_rejects_dot_output_dir``
            # for the regression-pin.
            if p.is_absolute() or ".." in p.parts or not p.parts:
                errors.append(
                    "phases.linting.output_dir must be a relative path "
                    "with at least one segment and no '..' segments "
                    "(resolved under <run_dir>/; values like '.' or "
                    "'./' that normalize to the run dir itself are "
                    "rejected)"
                )

    policy = lint.get("policy")
    if policy is not None and not _is_str(policy):
        errors.append(
            "phases.linting.policy must be a non-empty string when present"
        )

    include = lint.get("include")
    if include is not None and not _is_str(include):
        errors.append(
            "phases.linting.include must be a non-empty string when present"
        )

    exclude = lint.get("exclude")
    if exclude is not None and not _is_str(exclude):
        errors.append(
            "phases.linting.exclude must be a non-empty string when present"
        )


def _validate_documentation(config: Dict[str, Any], errors: List[str]) -> None:
    """Validate the phases.documentation subblock (format-only).

    Twin of :func:`_validate_linting`, but for the documentation phase.
    Values that touch the host filesystem (``aurig_doc_path``,
    ``tclsh_path``) are not checked here — existence is verified by
    :mod:`sentinel.documentation` at run time, per the module-level
    validation-vs-runtime contract. Any field that is present is
    validated; absent fields fall back to their runtime defaults inside
    :mod:`sentinel.documentation`.

    Note the documentation phase has no ``fail_on`` / ``policy`` /
    ``include`` / ``exclude`` — those are lint-specific. Documentation
    has no quality-threshold notion, so its format vocabulary is
    narrower too (``html`` / ``md`` only).
    """
    doc = (config.get("phases") or {}).get("documentation")
    if not isinstance(doc, dict):
        return

    aurig_doc_path = doc.get("aurig_doc_path")
    if aurig_doc_path is not None and not _is_str(aurig_doc_path):
        errors.append(
            "phases.documentation.aurig_doc_path must be a non-empty string when present"
        )

    tclsh_path = doc.get("tclsh_path")
    if tclsh_path is not None and not _is_str(tclsh_path):
        errors.append(
            "phases.documentation.tclsh_path must be a non-empty string when present"
        )

    fmt = doc.get("format")
    if fmt is not None and fmt not in VALID_DOCUMENTATION_FORMATS:
        errors.append(
            "phases.documentation.format must be one of: "
            f"{', '.join(sorted(VALID_DOCUMENTATION_FORMATS))}"
        )

    output_dir = doc.get("output_dir")
    if output_dir is not None:
        if not _is_str(output_dir):
            errors.append(
                "phases.documentation.output_dir must be a non-empty string when present"
            )
        else:
            # output_dir is resolved under <run_dir>/ at runtime; an
            # absolute path, a ``..`` segment, or a value that
            # normalizes to ``.`` would let the phase write outside the
            # run directory. Catch it at config-load time — same rule as
            # phases.linting.output_dir (see _validate_linting for the
            # full rationale and the empty-parts ``.`` / ``./`` note).
            p = Path(output_dir)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                errors.append(
                    "phases.documentation.output_dir must be a relative path "
                    "with at least one segment and no '..' segments "
                    "(resolved under <run_dir>/; values like '.' or "
                    "'./' that normalize to the run dir itself are "
                    "rejected)"
                )


def _validate_output(config: Dict[str, Any], errors: List[str]) -> None:
    output = config.get("output")
    if output is None:
        return
    if not isinstance(output, dict):
        errors.append("output must be a mapping")
        return
    base_dir = output.get("base_dir", "./runs")
    if not _is_str(base_dir):
        errors.append("output.base_dir must be a non-empty string")
    bundle = output.get("bundle_zip", True)
    if not isinstance(bundle, bool):
        errors.append("output.bundle_zip must be a boolean")


def validate_config(config: Dict[str, Any]) -> None:
    """Validate a parsed YAML config against the v1.0 schema.

    Raises ConfigValidationError(is_hard=False) listing every problem found.
    A return without exception means the config is structurally valid; phase
    modules may still discover runtime issues later.
    """
    logger = logging.getLogger(__name__)
    errors: List[str] = []

    if not isinstance(config, dict):
        raise ConfigValidationError(["config root must be a mapping"], is_hard=False)

    _validate_schema_version(config, errors)
    _validate_project(config, errors)
    _validate_global_settings(config, errors)
    _validate_fetch(config, errors)
    _validate_project_manifest(config, errors)
    _validate_pre_run(config, errors)
    _validate_phases(config, errors)
    _validate_linting(config, errors)
    _validate_documentation(config, errors)
    _validate_output(config, errors)

    if errors:
        raise ConfigValidationError(errors, is_hard=False)

    logger.debug("Configuration validation passed")


# ---------------------------------------------------------------------------
# Night-time window
# ---------------------------------------------------------------------------

def is_within_night_time_window(config: Dict[str, Any]) -> bool:
    """Return True if execution is allowed under the configured time window.

    No window configured -> always True. Cross-midnight windows
    (e.g. start=22:00, end=06:00) are handled correctly. An invalid
    window is treated as 'no restriction' and logged as a warning.
    """
    gs = config.get("global_settings") or {}
    window = gs.get("night_time_window")
    if not window:
        return True

    try:
        start = datetime.strptime(window["start"], "%H:%M").time()
        end = datetime.strptime(window["end"], "%H:%M").time()
    except (KeyError, TypeError, ValueError):
        logging.warning("Invalid night_time_window, ignoring restriction")
        return True

    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_configs(directory: str) -> List[str]:
    """List YAML configs in *directory* (top-level only).

    Returns absolute paths to files matching ``*.yaml`` directly under
    *directory*, sorted alphabetically. Hidden files (``.*``), editor
    swap files (``*.yaml.swp``), and anything inside subdirectories
    (including the conventional ``disabled/``) are ignored. The
    directory itself does not need to exist; in that case an empty list
    is returned.

    No validation is performed: callers must validate each entry.
    """
    base = Path(directory)
    if not base.is_dir():
        return []

    results: List[str] = []
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        if name.startswith("."):
            continue
        if not name.endswith(".yaml"):
            continue
        if name.endswith(".yaml.swp"):
            continue
        results.append(str(entry.resolve()))

    results.sort()
    return results
