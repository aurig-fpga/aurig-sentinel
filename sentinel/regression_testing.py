# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

"""Regression testing phase with vunit and convention backends.

Reads phases.regression from the YAML schema. Repo files are looked up
under ``ctx.repo_root`` (the fetched repo when available, otherwise the
``<run_dir>/repos`` placeholder).
"""

import importlib
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Optional

from .run_context import RunContext


def _vunit_is_importable() -> bool:
    """Return True iff ``vunit-hdl`` is installed and exposes ``VUnit``.

    A bare ``importlib.util.find_spec("vunit") is not None`` would
    accept any module named ``vunit`` on ``sys.path`` — a stray local
    ``vunit.py``, a partial install that lacks the ``VUnit`` class,
    or a package whose import side-effects raise — so it would set
    ``HAS_VUNIT`` true in cases where ``_run_vunit_backend`` would
    later fail with a much less targeted error from the user's
    runner script.

    Actually attempting the import (with broad exception catching)
    matches the semantics of the previous ``try: from vunit import
    VUnit`` shim, while still honouring OP-014 by not binding the
    ``VUnit`` symbol at module scope — we look it up via
    :py:func:`getattr` on the imported module and discard the
    reference once we've answered the question.
    """
    try:
        module = importlib.import_module("vunit")
        # Use getattr-with-default rather than hasattr because PEP 562
        # modules can define __getattr__ that raises non-AttributeError
        # exceptions; hasattr would propagate those out of the try
        # block and crash module load. getattr's default kicks in only
        # on AttributeError, so the surrounding except still has to
        # catch the broader case.
        return getattr(module, "VUnit", None) is not None
    except Exception:  # pragma: no cover - exhaustive defence
        return False


HAS_VUNIT = _vunit_is_importable()


def regression_testing_phase(
    config: Dict[str, Any], ctx: RunContext
) -> Optional[Dict[str, Any]]:
    """Run regression tests using vunit or convention backend.

    Returns a dict with status / output_dir / counters, or a small dict
    with status='skipped' when the phase is disabled.
    """
    logger = logging.getLogger(__name__)
    regression_cfg = (config.get("phases") or {}).get("regression") or {}

    if not regression_cfg.get("enabled", False):
        logger.info("Regression testing phase disabled in configuration")
        return {"status": "skipped", "message": "Phase disabled"}

    backend = regression_cfg.get("backend", "convention").lower()

    if backend == "vunit":
        return _run_vunit_backend(regression_cfg, ctx, logger)
    elif backend == "convention":
        return _run_convention_backend(regression_cfg, ctx, logger)
    else:
        logger.error("Unknown regression testing backend: %s", backend)
        return {"status": "error", "message": f"Unknown backend: {backend}"}


def _run_vunit_backend(regression_cfg: Dict[str, Any], ctx: RunContext,
                       logger: logging.Logger) -> Dict[str, Any]:
    """Run tests using VUnit framework."""
    if not HAS_VUNIT:
        logger.error("VUnit backend selected but vunit-hdl is not installed")
        return {"status": "error", "message": "VUnit not installed"}

    vunit_script = regression_cfg.get("vunit_run_script")
    if not vunit_script:
        logger.error("vunit_run_script not configured")
        return {"status": "error", "message": "vunit_run_script missing"}

    repo_root = ctx.repo_root
    if ctx.repo_path is None and (
        ctx.fetch_attempted
        or not repo_root.is_dir()
        or not any(repo_root.iterdir())
    ):
        # ctx.repo_root falls back to <run_dir>/repos when repo_path is
        # unset; that fallback is part of the public contract for
        # callers that pre-stage sources without running fetch. We
        # short-circuit only when:
        #   - fetch was attempted but didn't set repo_path (failed
        #     fetch — any residue is partial/untrusted), OR
        #   - the placeholder doesn't exist or is empty (nothing to do).
        # Populated placeholder + no fetch attempt = pre-stage, fall
        # through to the per-backend script-not-found checks. The
        # diagnostic is split so the user knows which side they're on
        # without having to read the fetch logs.
        if ctx.fetch_attempted:
            logger.error(
                "VUnit backend cannot run: fetch phase ran but produced "
                "no usable repository (see fetch logs)"
            )
            return {
                "status": "error",
                "message": "Fetch phase ran but produced no usable repository",
            }
        logger.error(
            "VUnit backend cannot run: no repository available "
            "(enable the fetch phase or pre-stage sources under repos_root)"
        )
        return {
            "status": "error",
            "message": (
                "No repository available; enable the fetch phase "
                "or pre-stage sources under repos_root"
            ),
        }

    script_path = repo_root / vunit_script
    if not script_path.exists():
        logger.error("VUnit script not found: %s", script_path)
        return {"status": "error", "message": f"VUnit script not found: {vunit_script}"}

    output_dir = ctx.output_dir(regression_cfg.get("work_dir", "regression_output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    simulator = regression_cfg.get("simulator", "ghdl")

    # Build VUnit command. Use the active interpreter (sys.executable)
    # rather than the literal "python": on hosts where Python is only
    # exposed as `python3` or where the venv is not on PATH, "python"
    # would either ENOENT or pick up the system interpreter without
    # the venv's installed packages (including vunit-hdl itself).
    vunit_args = regression_cfg.get("vunit_args", [])
    command = [
        sys.executable,
        str(script_path),
        f"--output-path={output_dir}",
        f"--simulator={simulator}",
        *vunit_args
    ]

    logger.info("Running VUnit: %s", ' '.join(command))

    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )

        # Write output to log
        log_file = output_dir / "vunit_output.log"
        with open(log_file, 'w') as f:
            f.write(f"Command: {' '.join(command)}\n\n")
            f.write("=== STDOUT ===\n")
            f.write(result.stdout)
            f.write("\n=== STDERR ===\n")
            f.write(result.stderr)

        if result.returncode == 0:
            logger.info("VUnit tests passed")
            return {
                "status": "completed",
                "backend": "vunit",
                "output_dir": str(output_dir),
                "log_file": str(log_file)
            }
        else:
            logger.error("VUnit tests failed with exit code %s", result.returncode)
            return {
                "status": "failed",
                "backend": "vunit",
                "exit_code": result.returncode,
                "output_dir": str(output_dir),
                "log_file": str(log_file)
            }

    except subprocess.TimeoutExpired:
        logger.error("VUnit tests timed out")
        return {"status": "failed", "backend": "vunit", "error": "Timeout"}
    except Exception as e:
        logger.error("VUnit execution failed: %s", e)
        return {"status": "failed", "backend": "vunit", "error": str(e)}


def _run_convention_backend(regression_cfg: Dict[str, Any], ctx: RunContext,
                            logger: logging.Logger) -> Dict[str, Any]:
    """Run tests using convention backend (testbench discovery + sim scripts)."""
    simulator = regression_cfg.get("simulator", "modelsim").lower()
    valid_simulators = ["modelsim", "questasim", "ghdl", "active-hdl", "vivado"]

    if simulator not in valid_simulators:
        logger.error("Unsupported simulator: %s", simulator)
        return {"status": "error", "message": f"Unsupported simulator: {simulator}"}

    testbench_dir = regression_cfg.get("testbench_dir")
    if not testbench_dir:
        logger.error("testbench_dir not configured")
        return {"status": "error", "message": "testbench_dir missing"}

    repo_root = ctx.repo_root
    if ctx.repo_path is None and (
        ctx.fetch_attempted
        or not repo_root.is_dir()
        or not any(repo_root.iterdir())
    ):
        # See note in _run_vunit_backend. A populated repos_root only
        # counts as pre-stage when fetch was NOT attempted — otherwise
        # the content is failed-fetch residue and must not mask the
        # real diagnostic behind a generic "Testbench directory not
        # found".
        if ctx.fetch_attempted:
            logger.error(
                "Convention backend cannot run: fetch phase ran but produced "
                "no usable repository (see fetch logs)"
            )
            return {
                "status": "error",
                "message": "Fetch phase ran but produced no usable repository",
            }
        logger.error(
            "Convention backend cannot run: no repository available "
            "(enable the fetch phase or pre-stage sources under repos_root)"
        )
        return {
            "status": "error",
            "message": (
                "No repository available; enable the fetch phase "
                "or pre-stage sources under repos_root"
            ),
        }

    tb_path = repo_root / testbench_dir
    if not tb_path.exists():
        logger.error("Testbench directory not found: %s", tb_path)
        return {"status": "error", "message": f"Testbench directory not found: {testbench_dir}"}

    # Discover testbenches
    testbenches = []
    for pattern in ["tb_*.vhd", "tb_*.vhdl"]:
        testbenches.extend(tb_path.glob(pattern))

    if not testbenches:
        logger.warning("No testbenches found in %s", tb_path)
        return {"status": "skipped", "message": "No testbenches found"}

    logger.info("Found %d testbenches", len(testbenches))

    output_dir = ctx.output_dir(regression_cfg.get("work_dir", "regression_output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    log2file = regression_cfg.get("options", {}).get("log2file_sim_output", True)

    # Run each testbench
    results = []
    passed = 0
    failed = 0
    skipped = 0

    for tb_file in testbenches:
        tb_name = tb_file.stem  # e.g., "tb_uart"
        sim_script = tb_path / f"{tb_name}_sim.tcl"

        if not sim_script.exists():
            logger.error("Simulation script not found: %s", sim_script)
            results.append({"testbench": tb_name, "status": "FAILED", "error": "Missing sim script"})
            failed += 1
            continue

        # Run simulation
        result = _run_single_simulation(
            tb_name, sim_script, simulator, output_dir, log2file, logger
        )
        results.append(result)

        if result["status"] == "PASSED":
            passed += 1
        elif result["status"] == "FAILED":
            failed += 1
        else:
            skipped += 1

    # Generate summary
    summary_file = output_dir / "regression_summary.txt"
    with open(summary_file, 'w') as f:
        f.write("REGRESSION TEST SUMMARY\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Backend: convention\n")
        f.write(f"Simulator: {simulator}\n")
        f.write(f"Total: {len(results)}\n")
        f.write(f"Passed: {passed}\n")
        f.write(f"Failed: {failed}\n")
        f.write(f"Skipped: {skipped}\n\n")
        f.write("=" * 60 + "\n\n")

        for result in results:
            status = result["status"]
            tb = result["testbench"]
            f.write(f"{tb}: {status}\n")
            if "error" in result:
                f.write(f"  Error: {result['error']}\n")
            if "log_file" in result:
                f.write(f"  Log: {result['log_file']}\n")

    logger.info(
        "Regression summary: %d passed, %d failed, %d skipped",
        passed, failed, skipped,
    )

    overall_status = "completed" if failed == 0 else "failed"

    return {
        "status": overall_status,
        "backend": "convention",
        "simulator": simulator,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "output_dir": str(output_dir),
        "summary_file": str(summary_file),
        "results": results
    }


def _run_single_simulation(tb_name: str, sim_script: Path, simulator: str,
                           output_dir: Path, log2file: bool,
                           logger: logging.Logger) -> Dict[str, Any]:
    """Run a single simulation.

    Args:
        tb_name: Testbench name
        sim_script: Path to simulation TCL script
        simulator: Simulator to use
        output_dir: Output directory
        log2file: Whether to save log to file
        logger: Logger instance

    Returns:
        Dictionary with simulation result
    """
    logger.info("Running testbench: %s", tb_name)

    # Build simulator command (batch mode only)
    if simulator in ["modelsim", "questasim"]:
        command = ["vsim", "-c", "-do", str(sim_script)]
    elif simulator == "ghdl":
        # For GHDL, the TCL script should contain ghdl commands
        command = ["ghdl", "--elab-run", tb_name]
    elif simulator == "active-hdl":
        command = ["vsimsa", "-do", str(sim_script)]
    elif simulator == "vivado":
        command = ["xsim", tb_name, "-tclbatch", str(sim_script)]
    else:
        logger.error("Simulator %s command not configured", simulator)
        return {"testbench": tb_name, "status": "SKIPPED", "error": "Simulator not configured"}

    log_file = None
    if log2file:
        log_file = output_dir / f"{tb_name}.log"

    try:
        result = subprocess.run(
            command,
            cwd=sim_script.parent,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout per testbench
        )

        # Save output
        if log2file and log_file:
            with open(log_file, 'w') as f:
                f.write(f"Command: {' '.join(command)}\n\n")
                f.write("=== STDOUT ===\n")
                f.write(result.stdout)
                f.write("\n=== STDERR ===\n")
                f.write(result.stderr)

        if result.returncode == 0:
            logger.info("Testbench %s: PASSED", tb_name)
            return {
                "testbench": tb_name,
                "status": "PASSED",
                "exit_code": 0,
                "log_file": str(log_file) if log_file else None
            }
        else:
            logger.error(
                "Testbench %s: FAILED (exit code %s)",
                tb_name, result.returncode,
            )
            return {
                "testbench": tb_name,
                "status": "FAILED",
                "exit_code": result.returncode,
                "log_file": str(log_file) if log_file else None
            }

    except subprocess.TimeoutExpired:
        logger.error("Testbench %s: TIMEOUT", tb_name)
        return {"testbench": tb_name, "status": "FAILED", "error": "Timeout"}
    except FileNotFoundError:
        logger.error("Simulator not found: %s", command[0])
        return {"testbench": tb_name, "status": "FAILED", "error": f"Simulator not found: {command[0]}"}
    except Exception as e:
        logger.error("Testbench %s: Exception - %s", tb_name, e)
        return {"testbench": tb_name, "status": "FAILED", "error": str(e)}
