# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Process-level exit code contract for the ``run.py`` entrypoint.

The suite in ``test_sentinel_comprehensive.py`` asserts the value
``main()`` *returns*. That is necessary but not sufficient: it cannot
catch an entrypoint that computes the right code and then drops it on
the floor, which is precisely what ``run.py`` did (issue #10 — five of
six documented cases exited 0 because ``main.main()`` was evaluated as a
bare statement).

So every test here spawns ``run.py`` as a real subprocess and asserts
``CompletedProcess.returncode``. The contract under test is the table at
README.md "Exit codes": 0 = no execution failures, 1 = at least one
execution failure, 2 = misconfiguration with no phases run.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


RUN_PY = Path(__file__).resolve().parent.parent / "run.py"

# Generous enough for a cold interpreter start on Windows CI, short
# enough that a hang fails the job instead of burning the 10 minute
# timeout.
_TIMEOUT_SECONDS = 120


def _run_sentinel(*args, cwd):
    """Invoke run.py in a subprocess and return the CompletedProcess.

    ``sys.executable`` rather than "python" so the test runs under the
    same interpreter pytest does — the CI matrix spans 3.9-3.13 on both
    ubuntu and windows, and "python" may be absent or point elsewhere.
    No ``shell=True``: arguments are passed as a list so paths
    containing spaces survive on Windows.
    """
    env = os.environ.copy()
    # Discovery falls back to $SENTINEL_CONFIGS_DIR when no --config /
    # --config-dir is given (sentinel/main.py:_resolve_config_dir). A
    # value inherited from the developer's shell would silently change
    # which configs a test sees, so drop it.
    env.pop("SENTINEL_CONFIGS_DIR", None)
    return subprocess.run(
        [sys.executable, str(RUN_PY), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_SECONDS,
    )


def _config(name, local_path, base_dir, continue_on_error=False):
    """A schema v1.0 config that passes validation.

    ``local_path`` is written as POSIX even on Windows: a Windows path
    in a YAML plain scalar carries literal backslashes, and forward
    slashes are accepted by pathlib on every platform. Keeps the fixture
    free of quoting games.

    ``continue_on_error: false`` matters for the execution-failure case:
    it makes a failed fetch abort the pipeline before synthesis is
    reached, so no test here needs Vivado (or any vendor tool) present.
    """
    return textwrap.dedent(f"""\
        schema_version: "1.0"
        project:
          name: {name}
        global_settings:
          continue_on_error: {str(continue_on_error).lower()}
        fetch:
          type: local
          local_path: {Path(local_path).as_posix()}
        project_manifest: manifest/sentinel.yaml
        phases:
          synthesis:
            enabled: true
            synthesis_tool: vivado
            synthesis_tool_path: {Path(base_dir).as_posix()}/nonexistent_vivado
            synthesis_script: vivado
            repo_synthesis_script: scripts/run_synthesis.tcl
            output_dir: synthesis_output
        output:
          base_dir: {Path(base_dir).as_posix()}/runs
          bundle_zip: false
        """)


@pytest.fixture
def workspace(tmp_path):
    """An isolated config dir + output dir under pytest's tmp_path.

    Nothing is ever written into the repository's own configs/.
    """
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    return tmp_path, cfg_dir


# ---------------------------------------------------------------------------
# 0 — no execution failures
# ---------------------------------------------------------------------------

def test_successful_dry_run_exits_0(workspace):
    """Control. Without this, a fix that hardcoded a non-zero exit
    would pass every other test in this file.
    """
    tmp_path, cfg_dir = workspace
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cfg = cfg_dir / "ok.yaml"
    cfg.write_text(_config("ok_project", checkout, tmp_path), encoding="utf-8")

    result = _run_sentinel("--config", str(cfg), "--dry-run", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "0 failed (execution)" in result.stdout


# ---------------------------------------------------------------------------
# 1 — at least one execution failure
# ---------------------------------------------------------------------------

def test_execution_failure_exits_1(workspace):
    """A config that validates, executes, and fails during execution.

    The distinction is load-bearing. README's table says a config
    *skipped* by validation or *blocked* by the night window does not
    escalate the exit code, so a fixture that merely failed validation
    would assert 0 and prove nothing about the 1 case.

    ``fetch.local_path`` points at a directory that does not exist.
    config_validator deliberately leaves that to run time (see its note
    that the runner "is responsible for surfacing a missing local_path
    at run time"), so the config passes validation and then fails in
    the fetch phase. The assertions on stdout below pin that down: the
    run must report zero skipped and a non-zero failed (execution)
    count, which is only reachable by actually executing.
    """
    tmp_path, cfg_dir = workspace
    cfg = cfg_dir / "broken.yaml"
    cfg.write_text(
        _config("broken_project", tmp_path / "does_not_exist_checkout", tmp_path),
        encoding="utf-8",
    )

    result = _run_sentinel("--config", str(cfg), cwd=tmp_path)

    assert result.returncode == 1, result.stderr
    # Proof the run reached execution rather than being skipped or blocked.
    assert "0 skipped (validation)" in result.stdout
    assert "0 blocked (night-window)" in result.stdout
    assert "1 failed (execution)" in result.stdout


# ---------------------------------------------------------------------------
# 2 — misconfiguration, no phases run
# ---------------------------------------------------------------------------

def test_nonexistent_config_file_exits_2(workspace):
    tmp_path, cfg_dir = workspace
    ghost = cfg_dir / "does_not_exist.yaml"

    result = _run_sentinel("--config", str(ghost), cwd=tmp_path)

    assert result.returncode == 2, result.stdout
    assert "config file not found" in result.stderr


def test_empty_config_dir_exits_2(workspace):
    """The empty-directory case the issue singled out: the tool printed
    "yields exit 2" and then exited 0.
    """
    tmp_path, _ = workspace
    empty = tmp_path / "empty_configs"
    empty.mkdir()

    result = _run_sentinel("--config-dir", str(empty), cwd=tmp_path)

    assert result.returncode == 2, result.stdout
    assert "no active config files found" in result.stderr


def test_nonexistent_config_dir_exits_2(workspace):
    tmp_path, _ = workspace
    ghost_dir = tmp_path / "no_such_dir"

    result = _run_sentinel("--config-dir", str(ghost_dir), cwd=tmp_path)

    assert result.returncode == 2, result.stdout
    assert "config directory not found" in result.stderr


def test_duplicate_project_name_exits_2(workspace):
    tmp_path, cfg_dir = workspace
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for filename in ("alpha.yaml", "beta.yaml"):
        (cfg_dir / filename).write_text(
            _config("duplicate_name", checkout, tmp_path), encoding="utf-8"
        )

    result = _run_sentinel("--config-dir", str(cfg_dir), "--dry-run", cwd=tmp_path)

    assert result.returncode == 2, result.stdout
    assert "project.name must be unique" in result.stderr
    # The collision must short-circuit before the per-config loop.
    assert "config(s) processed" not in result.stdout


def test_conflicting_config_flags_exit_2(workspace):
    """The one case that was already correct before the fix, because
    argparse raises SystemExit(2) from inside main() and an exception
    propagates regardless of whether the caller keeps the return value.
    Kept so a future refactor of the entrypoint cannot regress it.
    """
    tmp_path, cfg_dir = workspace
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    cfg = cfg_dir / "ok.yaml"
    cfg.write_text(_config("ok_project", checkout, tmp_path), encoding="utf-8")

    result = _run_sentinel(
        "--config", str(cfg), "--config-dir", str(cfg_dir), cwd=tmp_path
    )

    assert result.returncode == 2, result.stdout
    assert "mutually exclusive" in result.stderr
