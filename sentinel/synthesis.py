# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

import os
import subprocess
import logging
from typing import Dict, Any, Optional

from .run_context import RunContext


def run_synthesis(config: Dict[str, Any], ctx: RunContext) -> Optional[Dict[str, Any]]:
    """Run FPGA synthesis using a repository-provided custom script.

    Reads phases.synthesis from the YAML schema. Sentinel sets up the
    environment (PATH, working directory) and invokes the vendor tool in
    batch mode against the repo's own synthesis script.
    """
    logger = logging.getLogger(__name__)
    synthesis_config = (config.get("phases") or {}).get("synthesis") or {}

    if not synthesis_config.get("enabled", False):
        logger.info("Synthesis phase disabled in configuration")
        return {"status": "skipped", "message": "Phase disabled"}

    synthesis_tool = synthesis_config.get('synthesis_tool', 'vivado')

    # Dispatch to appropriate synthesis tool
    if synthesis_tool.lower() == 'vivado':
        return _run_vivado_synthesis_custom(synthesis_config, ctx, logger)
    elif synthesis_tool.lower() == 'quartus':
        return _run_quartus_synthesis_custom(synthesis_config, ctx, logger)
    else:
        logger.error("Unsupported synthesis tool: %s", synthesis_tool)
        return {"status": "error", "message": f"Unsupported synthesis tool: {synthesis_tool}"}


def _run_vivado_synthesis_custom(synthesis_config: Dict[str, Any], ctx: RunContext,
                                  logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """Run Vivado synthesis using repository-provided custom script.

    Per requirements:
    - Repository must provide synthesis script (e.g., scripts/run_synthesis.tcl)
    - Script must handle: import RTL, set top/device, run synthesis, run DRC, write reports
    - Script must exit with 0 on success, non-zero on failure
    - Sentinel runs in batch mode only
    """

    logger.info("Starting Vivado synthesis phase with custom script")

    # Get synthesis tool path
    synthesis_tool_path = synthesis_config.get('synthesis_tool_path')
    if not synthesis_tool_path:
        logger.error("synthesis_tool_path not configured")
        return {"status": "error", "message": "synthesis_tool_path missing"}

    if not os.path.exists(synthesis_tool_path):
        logger.error("Synthesis tool path not found: %s", synthesis_tool_path)
        return {"status": "error", "message": f"Tool path not found: {synthesis_tool_path}"}

    repo_root_path = ctx.repo_root
    if ctx.repo_path is None and (
        ctx.fetch_attempted
        or not repo_root_path.is_dir()
        or not any(repo_root_path.iterdir())
    ):
        # Fall through only when the placeholder is genuinely
        # pre-staged: fetch was never attempted AND repos_root has
        # real content. Otherwise (fetch ran and failed, or no
        # content at all) surface the targeted error instead of the
        # downstream "Repository synthesis script not found".
        if ctx.fetch_attempted:
            logger.error(
                "Vivado synthesis cannot run: fetch phase ran but produced "
                "no usable repository (see fetch logs)"
            )
            return {
                "status": "error",
                "message": "Fetch phase ran but produced no usable repository",
            }
        logger.error(
            "Vivado synthesis cannot run: no repository available "
            "(enable the fetch phase or pre-stage sources under repos_root)"
        )
        return {
            "status": "error",
            "message": (
                "No repository available; enable the fetch phase "
                "or pre-stage sources under repos_root"
            ),
        }

    # Get output directory
    output_dir = str(ctx.output_dir(synthesis_config.get('output_dir', 'synthesis_output')))
    os.makedirs(output_dir, exist_ok=True)

    repo_root = str(repo_root_path)

    # Get synthesis script path from config
    repo_script = synthesis_config.get('repo_synthesis_script')
    if not repo_script:
        # Try common locations
        possible_scripts = [
            'scripts/run_synthesis.tcl',
            'scripts/synthesis.tcl',
            'syn/run_synthesis.tcl',
            'build/synthesis.tcl'
        ]
        for script in possible_scripts:
            script_path = os.path.join(repo_root, script)
            if os.path.exists(script_path):
                repo_script = script_path
                break
    else:
        script_path = os.path.join(repo_root, repo_script)
        if not os.path.exists(script_path):
            logger.error("Configured synthesis script not found: %s", script_path)
            return {"status": "error", "message": f"Synthesis script not found: {repo_script}"}
        repo_script = script_path

    if not repo_script or not os.path.exists(repo_script):
        logger.error("No synthesis script found in repository. Expected scripts/run_synthesis.tcl or similar.")
        return {"status": "error", "message": "Repository synthesis script not found"}

    logger.info("Using repository synthesis script: %s", repo_script)

    # Prepare environment - add synthesis tool to PATH
    env = os.environ.copy()
    vivado_bin = os.path.join(synthesis_tool_path, 'bin')
    if os.path.exists(vivado_bin):
        env['PATH'] = f"{vivado_bin}{os.pathsep}{env.get('PATH', '')}"
        logger.info("Prepending %s to PATH for Vivado", vivado_bin)

    # Get synthesis script executable
    synthesis_script = synthesis_config.get('synthesis_script', 'vivado')

    # Build batch mode command
    log_file = os.path.join(output_dir, 'vivado.log')
    journal_file = os.path.join(output_dir, 'vivado.jou')

    vivado_cmd = [
        synthesis_script,
        '-mode', 'batch',
        '-source', repo_script,
        '-log', log_file,
        '-journal', journal_file
    ]

    logger.info("Running Vivado: %s", ' '.join(vivado_cmd))
    logger.info("Working directory: %s", output_dir)

    try:
        result = subprocess.run(
            vivado_cmd,
            cwd=output_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=3600  # 1 hour timeout
        )

        # Write stdout/stderr to log
        combined_log = os.path.join(output_dir, 'synthesis_full.log')
        with open(combined_log, 'w') as f:
            f.write(f"Command: {' '.join(vivado_cmd)}\n\n")
            f.write("=== STDOUT ===\n")
            f.write(result.stdout)
            f.write("\n=== STDERR ===\n")
            f.write(result.stderr)

        if result.returncode == 0:
            logger.info("Vivado synthesis completed successfully")

            # Check for expected reports
            missing_reports = []
            expected_reports = synthesis_config.get('options', {}).get('expected_reports', [])
            for report in expected_reports:
                report_path = os.path.join(output_dir, report)
                if not os.path.exists(report_path):
                    missing_reports.append(report)

            if missing_reports:
                logger.warning("Expected reports not found: %s", missing_reports)

            return {
                "status": "completed",
                "tool": "vivado",
                "script": repo_script,
                "output_dir": output_dir,
                "log_file": log_file,
                "missing_reports": missing_reports
            }
        else:
            logger.error("Vivado synthesis failed with return code %s", result.returncode)
            logger.error("STDERR: %s", result.stderr)

            return {
                "status": "failed",
                "tool": "vivado",
                "exit_code": result.returncode,
                # Vendor stderr usually starts with a banner / licence
                # check / version dump; the actual failing step lands
                # at the tail. Take the last 500 chars so the snippet
                # is useful in summaries, and reference the combined
                # log so callers always have the full output when
                # 500 chars is not enough context.
                "error": result.stderr[-500:],
                "log_file": log_file,
                "full_log_file": combined_log,
            }

    except subprocess.TimeoutExpired:
        logger.error("Vivado synthesis timed out after 1 hour")
        return {"status": "failed", "error": "Timeout", "log_file": log_file}
    except FileNotFoundError:
        logger.error("Vivado not found. Check synthesis_tool_path: %s", synthesis_tool_path)
        return {"status": "failed", "error": "Vivado not found"}
    except Exception as e:
        logger.error("Synthesis failed with exception: %s", e)
        return {"status": "failed", "error": str(e)}


def _run_quartus_synthesis_custom(synthesis_config: Dict[str, Any], ctx: RunContext,
                                   logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """Run Quartus synthesis using repository-provided script (placeholder)."""
    logger.error("Quartus synthesis with custom scripts not yet implemented")
    return {"status": "error", "message": "Quartus not implemented"}
