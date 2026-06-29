#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

################################################################################
# Sentinel System-wide Installation Script
#
# Purpose:
#   Installs Sentinel FPGA build pipeline as a production system service
#   following Linux Filesystem Hierarchy Standard (FHS) best practices.
#
# What This Script Does:
#   1. Creates dedicated 'sentinel' system user (no login, secure)
#   2. Installs application to /opt/sentinel (FHS compliant)
#   3. Sets up configuration in /etc/sentinel
#   4. Creates data directory in /var/lib/sentinel
#   5. Creates log directory in /var/log/sentinel
#   6. Installs Python virtualenv and dependencies
#   7. Configures systemd service/timer units
#   8. Applies security hardening (permissions, systemd features)
#   9. Installs logrotate configuration
#
# Security Features:
#   - Dedicated system user with no login capability
#   - Application owned by root (immutable, sentinel cannot modify)
#   - Configuration owned by root (read-only for sentinel)
#   - Only data and logs writable by sentinel user
#   - systemd hardening: NoNewPrivileges, ProtectSystem, resource limits
#
# Requirements:
#   - Must run as root/sudo
#   - Python 3.x installed
#   - systemd-based Linux distribution
#
# Usage:
#   sudo ./install-system.sh
#
# Author: Sentinel Development Team
# Date: November 2025
################################################################################

set -e  # Exit immediately if any command fails

################################################################################
# Configuration Variables
################################################################################

# Determine script and source directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SENTINEL_SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Installation paths (FHS compliant)
INSTALL_DIR="/opt/sentinel"          # Application binaries and code
CONFIG_DIR="/etc/sentinel"            # Configuration files (read-only for service)
DATA_DIR="/var/lib/sentinel"          # Persistent data (projects, repos)
LOG_DIR="/var/log/sentinel"           # Log files (writable by service)
SYSTEMD_DIR="/etc/systemd/system"     # systemd unit files

# Service user/group (dedicated, non-login account)
SERVICE_USER="sentinel"
SERVICE_GROUP="sentinel"

################################################################################
# Pre-flight Checks
################################################################################

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     Sentinel FPGA Pipeline - System-wide Installation          ║"
echo "║                   (FHS Compliant)                              ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "This script will install Sentinel as a system-wide service:"
echo
echo "  Application:    $INSTALL_DIR"
echo "  Configuration:  $CONFIG_DIR"
echo "  Data/Projects:  $DATA_DIR"
echo "  Logs:           $LOG_DIR"
echo "  Systemd units:  $SYSTEMD_DIR"
echo

# Check if running as root (required for system-wide installation)
if [ "$EUID" -ne 0 ]; then
    echo "❌ ERROR: This script must be run as root (use sudo)"
    echo "   Example: sudo ./install-system.sh"
    exit 1
fi

echo "[INFO] Running as root - system-wide installation"
echo

# Confirmation prompt (prevent accidental installation)
read -p "Proceed with installation? [y/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 0
fi

################################################################################
# Step 1: Create Dedicated System User
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    Step 1: Create User/Group                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Create system user if it doesn't exist
# This user has:
#   - No home directory (uses /opt/sentinel instead)
#   - No login shell (/bin/false prevents SSH/console login)
#   - No password (cannot authenticate)
#   - System UID (< 1000, not shown in login screens)
# Security: Even if compromised, attacker cannot login or escalate privileges
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "[INFO] Creating system user: $SERVICE_USER"
    useradd --system \
            --no-create-home \
            --shell /bin/false \
            --comment "Sentinel FPGA Build Service" \
            "$SERVICE_USER"
    echo "✅ User created: $SERVICE_USER (UID: $(id -u $SERVICE_USER))"
else
    echo "[INFO] User already exists: $SERVICE_USER (UID: $(id -u $SERVICE_USER))"
fi

################################################################################
# Step 2: Create FHS-Compliant Directory Structure
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   Step 2: Create Directories                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Create directories following Filesystem Hierarchy Standard (FHS):
#
# /opt/sentinel/          - Application code (immutable, root-owned)
# /etc/sentinel/          - Configuration files (read-only for service)
# /var/lib/sentinel/      - Persistent data (projects, repos)
#   ├── projects/         - Build outputs
#   └── repos/            - Git checkouts
# /var/log/sentinel/      - Log files (rotated by logrotate)
#
# Security model:
#   - Application and config owned by root (sentinel cannot modify)
#   - Only data and logs owned by sentinel (writable)
echo "[INFO] Creating directory structure..."

mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"/{projects,repos}  # Create subdirectories
mkdir -p "$LOG_DIR"

echo "✅ Directories created"

################################################################################
# Step 3: Copy Application Files
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  Step 3: Copy Application Files                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Copy application files from source to /opt/sentinel
# Excludes: tests, deployment configs, git files, Python cache
# Includes: sentinel/ package, pyproject.toml, LICENSE/NOTICE
echo "[INFO] Copying Sentinel application to $INSTALL_DIR..."

cp -r "$SENTINEL_SOURCE/sentinel" "$INSTALL_DIR/"
cp "$SENTINEL_SOURCE/pyproject.toml" "$INSTALL_DIR/"
cp "$SENTINEL_SOURCE/LICENSE" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SENTINEL_SOURCE/NOTICE" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SENTINEL_SOURCE/README.md" "$INSTALL_DIR/" 2>/dev/null || true

echo "✅ Application files copied"

################################################################################
# Step 4: Setup Python Virtual Environment
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                Step 4: Setup Python Virtual Environment        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Detect Python version (prefer 3.12, 3.11, 3.10, 3.9, fallback to python3)
# Virtual environment isolates Sentinel's dependencies from system Python
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v $cmd &> /dev/null; then
        PYTHON_CMD=$cmd
        echo "[FOUND] Python: $(command -v $cmd)"
        $cmd --version
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ ERROR: Python 3 not found!"
    exit 1
fi

# Create virtual environment
echo "[INFO] Creating virtual environment..."
cd "$INSTALL_DIR"
$PYTHON_CMD -m venv venv

# Activate and install
echo "[INFO] Installing Sentinel package..."
source venv/bin/activate
pip install --upgrade pip
pip install -e .
deactivate

echo "✅ Virtual environment configured"

################################################################################
# Step 5: Copy and Update Configuration
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    Step 5: Copy Configuration                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Copy configuration template to /etc/sentinel
# Automatically updates paths to use /var/lib/sentinel for data
# Admin must review and customize for their environment
if [ -f "$SENTINEL_SOURCE/config/sentinel_local.json" ]; then
    echo "[INFO] Copying configuration template..."
    cp "$SENTINEL_SOURCE/config/sentinel_local.json" "$CONFIG_DIR/config.json"

    # Update paths in config to use /var/lib/sentinel
    echo "[INFO] Updating configuration paths..."
    sed -i 's|"workspace_path": ".*"|"workspace_path": "/var/lib/sentinel"|g' "$CONFIG_DIR/config.json" 2>/dev/null || true

    echo "✅ Configuration copied to $CONFIG_DIR/config.json"
    echo "⚠️  IMPORTANT: Review and edit $CONFIG_DIR/config.json"
else
    echo "[WARNING] No configuration template found"
    echo "⚠️  You'll need to create $CONFIG_DIR/config.json manually"
fi

################################################################################
# Step 6: Set Permissions (Security Critical)
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   Step 6: Set Permissions                      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

echo "[INFO] Setting ownership and permissions..."

# Set ownership (implements least-privilege security model):
#   /opt/sentinel:      root:root       → sentinel cannot modify code
#   /etc/sentinel:      root:root       → sentinel cannot change config
#   /var/lib/sentinel:  sentinel:sentinel → sentinel CAN write data
#   /var/log/sentinel:  sentinel:sentinel → sentinel CAN write logs
chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$LOG_DIR"
chown -R root:root "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR"/*.json 2>/dev/null || true

echo "✅ Permissions set"

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  Step 7: Install Systemd Units                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Copy systemd units
echo "[INFO] Installing systemd service and timer..."

cp "$SCRIPT_DIR/sentinel.service" "$SYSTEMD_DIR/sentinel.service"
cp "$SCRIPT_DIR/sentinel.timer" "$SYSTEMD_DIR/sentinel.timer"

chmod 644 "$SYSTEMD_DIR/sentinel.service"
chmod 644 "$SYSTEMD_DIR/sentinel.timer"

# Install logrotate configuration
echo "[INFO] Installing logrotate configuration..."
if [ -f "$SCRIPT_DIR/sentinel-logrotate.conf" ]; then
    cp "$SCRIPT_DIR/sentinel-logrotate.conf" /etc/logrotate.d/sentinel
    chmod 644 /etc/logrotate.d/sentinel
    echo "✅ Logrotate configuration installed"
else
    echo "[WARNING] Logrotate config not found (optional)"
fi

echo "✅ Systemd units installed"

# Reload systemd
echo "[INFO] Reloading systemd daemon..."
systemctl daemon-reload
echo "✅ Systemd reloaded"

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              Step 8: Create Global Commands                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Create symlinks for global commands
echo "[INFO] Creating global commands for all users..."

# Sentinel command
if [ -f "$INSTALL_DIR/venv/bin/sentinel" ]; then
    ln -sf "$INSTALL_DIR/venv/bin/sentinel" /usr/local/bin/sentinel
    echo "✅ Created: /usr/local/bin/sentinel"
else
    echo "[WARNING] sentinel command not found in venv"
fi

# Sentinel-setup command
if [ -f "$SCRIPT_DIR/setup-user.sh" ]; then
    cp "$SCRIPT_DIR/setup-user.sh" /usr/local/bin/sentinel-setup
    chmod +x /usr/local/bin/sentinel-setup
    echo "✅ Created: /usr/local/bin/sentinel-setup"
else
    echo "[WARNING] setup-user.sh not found (optional)"
fi

echo "✅ Global commands installed - all users can now run 'sentinel' and 'sentinel-setup'"

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   Step 9: Enable/Start Service                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Ask about service vs timer
echo "Choose startup method:"
echo "  1) Timer only (scheduled - recommended)"
echo "  2) Service only (continuous running)"
echo "  3) Both timer and service"
echo "  4) Neither (manual configuration)"
read -p "Enter choice [1-4]: " STARTUP_CHOICE

case $STARTUP_CHOICE in
    1)
        systemctl enable sentinel.timer
        echo "[INFO] Timer enabled. Start now? [y/N]"
        read -p "> " START_NOW
        if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
            systemctl start sentinel.timer
            echo "✅ Timer started"
        fi
        ;;
    2)
        systemctl enable sentinel.service
        echo "[INFO] Service enabled. Start now? [y/N]"
        read -p "> " START_NOW
        if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
            systemctl start sentinel.service
            echo "✅ Service started"
        fi
        ;;
    3)
        systemctl enable sentinel.timer sentinel.service
        echo "[INFO] Both enabled. Start now? [y/N]"
        read -p "> " START_NOW
        if [[ "$START_NOW" =~ ^[Yy]$ ]]; then
            systemctl start sentinel.timer sentinel.service
            echo "✅ Timer and service started"
        fi
        ;;
    *)
        echo "[INFO] Skipped auto-start configuration"
        ;;
esac

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  Installation Complete!                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "📁 Directory Structure (FHS Compliant):"
echo "   $INSTALL_DIR/         - Application files (read-only)"
echo "   $CONFIG_DIR/          - Configuration files"
echo "   $DATA_DIR/            - Project data and repositories"
echo "   $LOG_DIR/             - Log files"
echo
echo "👤 Service runs as user: $SERVICE_USER"
echo
echo "🌍 Global Commands (available to all users):"
echo "   sentinel          - Run Sentinel with custom config"
echo "   sentinel-setup    - One-command setup for regular users"
echo
echo "📝 Next Steps:"
echo "   1. Review configuration: sudo nano $CONFIG_DIR/config.json"
echo "   2. Update paths in config (repos, output directories, etc.)"
echo "   3. Test manually: sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python -m sentinel.main -config $CONFIG_DIR/config.json"
echo
echo "👥 For Regular Users:"
echo "   Users can now run: sentinel-setup"
echo "   This creates their own config without copying code!"
echo "   See: BEST_PRACTICES_MULTI_USER.md"
echo
echo "📋 Management Commands:"
echo "   sudo systemctl status sentinel.timer     # Check timer status"
echo "   sudo systemctl start sentinel.timer      # Start timer"
echo "   sudo systemctl stop sentinel.timer       # Stop timer"
echo "   sudo systemctl status sentinel.service   # Check service status"
echo "   sudo journalctl -u sentinel.service -f   # View logs"
echo "   systemctl list-timers                    # List all timers"
echo
echo "🗑️  Uninstall:"
echo "   Run: sudo $SCRIPT_DIR/uninstall-system.sh"
echo
echo "✅ Installation successful!"
echo
