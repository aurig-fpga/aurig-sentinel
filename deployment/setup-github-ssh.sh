#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

################################################################################
# GitHub SSH Setup for Sentinel System User
#
# Purpose:
#   Configures SSH keys for the sentinel system user to access GitHub
#   repositories (both public and private).
#
# What This Script Does:
#   1. Creates /var/lib/sentinel/.ssh directory
#   2. Generates ED25519 SSH key pair (no passphrase for automation)
#   3. Creates SSH config for GitHub
#   4. Sets correct ownership (sentinel:sentinel) and permissions
#   5. Displays public key for GitHub configuration
#
# Usage:
#   sudo ./setup-github-ssh.sh
#
# After Running:
#   1. Copy the displayed public key
#   2. Add to GitHub as:
#      - Deploy Key (for single repo): Repo → Settings → Deploy keys
#      - SSH Key (for all repos): GitHub → Settings → SSH and GPG keys
#   3. Update /etc/systemd/system/sentinel.service to include:
#      Environment="HOME=/var/lib/sentinel"
#   4. Reload systemd: sudo systemctl daemon-reload
#   5. Test: sudo -u sentinel ssh -T git@github.com
#
# Security Notes:
#   - Key has no passphrase (required for automated git operations)
#   - Key stored in /var/lib/sentinel/.ssh (owned by sentinel user)
#   - Permissions set to 600 (read/write for owner only)
#   - StrictHostKeyChecking=accept-new (safer than 'no')
#
# Author: Sentinel Development Team
# Date: November 2025
################################################################################

set -e  # Exit on any error

################################################################################
# Configuration
################################################################################

SERVICE_USER="sentinel"
SERVICE_GROUP="sentinel"
SSH_DIR="/var/lib/sentinel/.ssh"
KEY_FILE="$SSH_DIR/id_ed25519"
SSH_CONFIG="$SSH_DIR/config"

################################################################################
# Pre-flight Checks
################################################################################

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║         GitHub SSH Setup for Sentinel System User             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ ERROR: This script must be run as root"
    echo "   Usage: sudo $0"
    exit 1
fi

# Check if sentinel user exists
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "❌ ERROR: User '$SERVICE_USER' does not exist"
    echo "   Run the system installation first:"
    echo "   sudo bash deployment/systemd/install-system.sh"
    exit 1
fi

echo "✅ Running as root"
echo "✅ Sentinel user exists (UID: $(id -u $SERVICE_USER))"
echo

################################################################################
# Step 1: Create SSH Directory
################################################################################

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    Step 1: Create SSH Directory                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

if [ ! -d "$SSH_DIR" ]; then
    echo "[INFO] Creating SSH directory: $SSH_DIR"
    mkdir -p "$SSH_DIR"
    echo "✅ Directory created"
else
    echo "[INFO] SSH directory already exists: $SSH_DIR"
fi

################################################################################
# Step 2: Generate SSH Key
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                    Step 2: Generate SSH Key                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

if [ -f "$KEY_FILE" ]; then
    echo "⚠️  SSH key already exists: $KEY_FILE"
    read -p "Overwrite existing key? [y/N]: " OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo "Keeping existing key."
        EXISTING_KEY=true
    else
        echo "[INFO] Removing old key..."
        rm -f "$KEY_FILE" "$KEY_FILE.pub"
        EXISTING_KEY=false
    fi
else
    EXISTING_KEY=false
fi

if [ "$EXISTING_KEY" = false ]; then
    echo "[INFO] Generating ED25519 SSH key..."
    echo "       (No passphrase for automated access)"
    
    ssh-keygen -t ed25519 \
        -C "sentinel@$(hostname)" \
        -f "$KEY_FILE" \
        -N "" \
        -q
    
    echo "✅ SSH key generated"
    echo "   Private key: $KEY_FILE"
    echo "   Public key:  $KEY_FILE.pub"
fi

################################################################################
# Step 3: Create SSH Config
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   Step 3: Create SSH Config                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

echo "[INFO] Creating SSH config for GitHub..."

cat > "$SSH_CONFIG" << 'EOF'
# SSH Configuration for GitHub Access
# Auto-generated by Sentinel setup script

Host github.com
    HostName github.com
    User git
    IdentityFile /var/lib/sentinel/.ssh/id_ed25519
    # Accept new host keys automatically (safer than StrictHostKeyChecking no)
    StrictHostKeyChecking accept-new
    # Store known hosts in sentinel's directory
    UserKnownHostsFile /var/lib/sentinel/.ssh/known_hosts
    # Connection settings
    ServerAliveInterval 60
    ServerAliveCountMax 3
    # Compression
    Compression yes

# Alternative: Multiple GitHub accounts (uncomment and configure if needed)
# Host github-work
#     HostName github.com
#     User git
#     IdentityFile /var/lib/sentinel/.ssh/id_ed25519_work
#     StrictHostKeyChecking accept-new
EOF

echo "✅ SSH config created: $SSH_CONFIG"

################################################################################
# Step 4: Set Ownership and Permissions
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║              Step 4: Set Ownership and Permissions             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

echo "[INFO] Setting ownership to $SERVICE_USER:$SERVICE_GROUP"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$SSH_DIR"

echo "[INFO] Setting secure permissions..."
# SSH directory: owner can read/write/execute, others have no access
chmod 700 "$SSH_DIR"

# Private key: owner can read/write, others have no access (CRITICAL)
if [ -f "$KEY_FILE" ]; then
    chmod 600 "$KEY_FILE"
fi

# Public key: owner read/write, others read-only
if [ -f "$KEY_FILE.pub" ]; then
    chmod 644 "$KEY_FILE.pub"
fi

# SSH config: owner can read/write, others have no access
chmod 600 "$SSH_CONFIG"

echo "✅ Permissions set:"
ls -la "$SSH_DIR"

################################################################################
# Step 5: Display Public Key and Instructions
################################################################################

echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                      Step 5: Setup Complete!                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "                         PUBLIC SSH KEY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
cat "$KEY_FILE.pub"
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

echo "NEXT STEPS:"
echo
echo "1️⃣  ADD KEY TO GITHUB:"
echo
echo "   Option A: Deploy Key (Single Repository - Recommended)"
echo "   --------------------------------------------------------"
echo "   • Go to: https://github.com/YOUR_USERNAME/YOUR_REPO/settings/keys"
echo "   • Click: 'Add deploy key'"
echo "   • Title: Sentinel Production Server ($(hostname))"
echo "   • Key: Paste the public key above"
echo "   • ✅ Allow write access (if Sentinel needs to push)"
echo "   • Click: 'Add key'"
echo
echo "   Option B: SSH Key (All Repositories in Account)"
echo "   ------------------------------------------------"
echo "   • Go to: https://github.com/settings/keys"
echo "   • Click: 'New SSH key'"
echo "   • Title: Sentinel Production Server ($(hostname))"
echo "   • Key type: Authentication Key"
echo "   • Key: Paste the public key above"
echo "   • Click: 'Add SSH key'"
echo
echo "2️⃣  UPDATE SYSTEMD SERVICE:"
echo
echo "   Edit: /etc/systemd/system/sentinel.service"
echo "   Add this line in the [Service] section:"
echo
echo "   Environment=\"HOME=/var/lib/sentinel\""
echo
echo "   Then reload:"
echo "   sudo systemctl daemon-reload"
echo
echo "3️⃣  TEST CONNECTION:"
echo
echo "   sudo -u sentinel ssh -T git@github.com"
echo
echo "   Expected output:"
echo "   'Hi username! You've successfully authenticated...'"
echo
echo "4️⃣  UPDATE SENTINEL CONFIG:"
echo
echo "   Edit: /etc/sentinel/sentinel.json"
echo "   Set repository URL to:"
echo
echo "   \"url\": \"git@github.com:YOUR_USERNAME/YOUR_REPO.git\""
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "📖 For detailed documentation, see:"
echo "   docs/GITHUB_SSH_SETUP_FOR_SENTINEL_USER.md"
echo
echo "✅ Setup complete!"
