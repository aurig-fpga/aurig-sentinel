#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

#
# Sentinel User Setup Script
# Sets up a regular user to use the system-wide Sentinel installation
# No code duplication - users only need their config files!
#

set -e

USER_HOME="${HOME}"
CONFIG_DIR="${USER_HOME}/.config/sentinel"
WORKSPACE_DIR="${USER_HOME}/sentinel_workspace"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           Sentinel User Setup                                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "User: $(whoami)"
echo "Home: ${USER_HOME}"
echo

# Check if sentinel command is available
if ! command -v sentinel &> /dev/null; then
    echo "❌ Error: 'sentinel' command not found!"
    echo "   The system administrator needs to install Sentinel system-wide first."
    echo
    echo "   Admin should run:"
    echo "   sudo /path/to/Sentinel/deployment/systemd/install-system.sh"
    echo
    exit 1
fi

echo "✅ Found sentinel command: $(which sentinel)"
echo

# Create config directory
echo "📁 Creating config directory: ${CONFIG_DIR}"
mkdir -p "${CONFIG_DIR}"

# Create workspace directory
echo "📁 Creating workspace directory: ${WORKSPACE_DIR}"
mkdir -p "${WORKSPACE_DIR}"

# Create example config
EXAMPLE_CONFIG="${CONFIG_DIR}/config.json"
if [ -f "${EXAMPLE_CONFIG}" ]; then
    echo "⚠️  Config already exists: ${EXAMPLE_CONFIG}"
    echo "   Skipping config creation. To recreate, delete it first."
else
    echo "📝 Creating example config: ${EXAMPLE_CONFIG}"
    cat > "${EXAMPLE_CONFIG}" << EOF
{
    "global_settings": {
        "project_name": "MyProject",
        "workspace_path": "${WORKSPACE_DIR}",
        "log_level": "verbose",
        "night_time_execution": false
    },
    "phases": {
        "fetch_code": {
            "enabled": false,
            "type": "git",
            "repo_url": "https://github.com/your-user/your-vhdl-project.git",
            "target_dir": "repos",
            "branch": "main"
        },
        "linting": {
            "enabled": false,
            "options": {
                "severity_level": "high"
            }
        },
        "documentation": {
            "enabled": false,
            "doc_tool": "sphinx",
            "doc_output_dir": "documentation"
        },
        "regression_testing": {
            "enabled": false,
            "simulator": "ghdl",
            "testbench_dir": "testbenches"
        },
        "synthesis": {
            "enabled": false,
            "synthesis_tool": "vivado",
            "target_device": "xc7z020clg400-1",
            "output_dir": "synthesis"
        },
        "deployment": {
            "enabled": false
        }
    }
}
EOF
    echo "✅ Created example config"
fi

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                     Setup Complete! ✅                          ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "Your Sentinel setup:"
echo "  • Config:    ${EXAMPLE_CONFIG}"
echo "  • Workspace: ${WORKSPACE_DIR}"
echo "  • Command:   sentinel"
echo
echo "Quick Start:"
echo
echo "  1. Edit your config:"
echo "     nano ${EXAMPLE_CONFIG}"
echo
echo "  2. Enable phases you want (fetch_code, linting, etc.)"
echo
echo "  3. Run Sentinel:"
echo "     sentinel -config ${EXAMPLE_CONFIG}"
echo
echo "  4. View results:"
echo "     ls -la ${WORKSPACE_DIR}/projects/"
echo "     cat ${WORKSPACE_DIR}/projects/*/runs/*/logs/sentinel.log"
echo
echo "Advanced: Create multiple configs for different projects:"
echo "  sentinel -config ~/my-fpga-project.json"
echo "  sentinel -config ~/another-project.json"
echo
echo "Monitor system service (if admin has it running):"
echo "  systemctl status sentinel.service"
echo
echo "Documentation:"
echo "  • User Guide: /opt/sentinel/README.md"
echo "  • Examples: Ask admin for example configs"
echo
