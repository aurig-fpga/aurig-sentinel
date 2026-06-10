#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

#
# Sentinel System-wide Uninstallation Script
# Removes Sentinel from /opt and cleans up all FHS directories
#

set -e

# Installation paths
INSTALL_DIR="/opt/sentinel"
CONFIG_DIR="/etc/sentinel"
DATA_DIR="/var/lib/sentinel"
LOG_DIR="/var/log/sentinel"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_USER="sentinel"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          Sentinel FPGA Pipeline - Uninstallation               ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "⚠️  WARNING: This will remove:"
echo "   • Application:   $INSTALL_DIR"
echo "   • Configuration: $CONFIG_DIR"
echo "   • Data/Projects: $DATA_DIR"
echo "   • Logs:          $LOG_DIR"
echo "   • Systemd units"
echo "   • User: $SERVICE_USER"
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ ERROR: This script must be run as root (use sudo)"
    exit 1
fi

# Confirmation
read -p "Are you sure you want to uninstall Sentinel? [y/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Uninstallation cancelled."
    exit 0
fi

echo
read -p "Delete project data in $DATA_DIR? [y/N]: " DELETE_DATA
echo

echo "[INFO] Stopping and disabling services..."
systemctl stop sentinel.service sentinel.timer 2>/dev/null || true
systemctl disable sentinel.service sentinel.timer 2>/dev/null || true
echo "✅ Services stopped and disabled"

echo
echo "[INFO] Removing systemd units..."
rm -f "$SYSTEMD_DIR/sentinel.service"
rm -f "$SYSTEMD_DIR/sentinel.timer"
systemctl daemon-reload
echo "✅ Systemd units removed"

echo
echo "[INFO] Removing application files..."
if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR"
    echo "✅ Removed: $INSTALL_DIR"
fi

if [ -d "$CONFIG_DIR" ]; then
    rm -rf "$CONFIG_DIR"
    echo "✅ Removed: $CONFIG_DIR"
fi

if [ -d "$LOG_DIR" ]; then
    rm -rf "$LOG_DIR"
    echo "✅ Removed: $LOG_DIR"
fi

if [[ "$DELETE_DATA" =~ ^[Yy]$ ]] && [ -d "$DATA_DIR" ]; then
    rm -rf "$DATA_DIR"
    echo "✅ Removed: $DATA_DIR"
else
    echo "⚠️  Preserved: $DATA_DIR (delete manually if needed)"
fi

echo
echo "[INFO] Removing system user..."
if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    userdel "$SERVICE_USER" 2>/dev/null || true
    echo "✅ Removed user: $SERVICE_USER"
fi

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  Uninstallation Complete                       ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "✅ Sentinel has been removed from the system"
echo

if [[ ! "$DELETE_DATA" =~ ^[Yy]$ ]]; then
    echo "ℹ️  Project data preserved in: $DATA_DIR"
    echo "   Delete manually with: sudo rm -rf $DATA_DIR"
fi
echo
