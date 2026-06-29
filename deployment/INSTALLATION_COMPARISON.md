# User-Level vs System-Wide Installation Comparison

## Quick Visual Comparison

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    USER-LEVEL INSTALLATION                               │
│                    (Development/Testing)                                 │
├─────────────────────────────────────────────────────────────────────────┤
│ Runs as:         it_admin (your username)                               │
│ Service type:    systemctl --user (stops when you log out*)             │
│ Log location:    ~/Sentinel/logs/                                       │
│ Data location:   ~/Sentinel/projects/                                   │
│ Config location: ~/Sentinel/config/                                     │
│ Installation:    deployment/systemd/install.sh                          │
│ Requires sudo:   No                                                     │
│                                                                          │
│ Log shows:       Running as user: it_admin (UID: 1001, GID: 1001)      │
│ Files owned by:  it_admin:it_admin                                      │
│ Service status:  systemctl --user status sentinel.service              │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                   SYSTEM-WIDE INSTALLATION                               │
│                   (Production/Server)                                    │
├─────────────────────────────────────────────────────────────────────────┤
│ Runs as:         sentinel (dedicated system user)                       │
│ Service type:    systemctl (system service, always runs)                │
│ Log location:    /var/log/sentinel/                                     │
│ Data location:   /var/lib/sentinel/                                     │
│ Config location: /etc/sentinel/                                         │
│ Installation:    deployment/systemd/install-system.sh                   │
│ Requires sudo:   Yes                                                    │
│                                                                          │
│ Log shows:       Running as user: sentinel (UID: 999, GID: 999)        │
│ Files owned by:  sentinel:sentinel                                      │
│ Service status:  sudo systemctl status sentinel.service                │
└─────────────────────────────────────────────────────────────────────────┘

*User-level services can persist with: loginctl enable-linger $USER
```

## How to Identify Which You Have

### Quick Check Command:
```bash
bash deployment/verify_user.sh
```

### Manual Checks:

1. **Check if sentinel user exists:**
   ```bash
   id sentinel
   ```
   - **User-level:** `id: 'sentinel': no such user`
   - **System-wide:** `uid=999(sentinel) gid=999(sentinel)...`

2. **Check where service is installed:**
   ```bash
   systemctl list-unit-files | grep sentinel      # System-wide
   systemctl --user list-unit-files | grep sentinel   # User-level
   ```

3. **Look at the first line of any log:**
   ```bash
   tail ~/Sentinel/logs/*.log  # User-level
   # or
   sudo tail /var/log/sentinel/sentinel.log  # System-wide
   ```

   You'll see:
   - `Running as user: it_admin` (user-level)
   - `Running as user: sentinel` (system-wide)

## Side-by-Side Command Comparison

| Task | User-Level | System-Wide |
|------|-----------|-------------|
| **Install** | `bash deployment/systemd/install.sh` | `sudo bash deployment/systemd/install-system.sh` |
| **Uninstall** | `bash deployment/systemd/uninstall.sh` | `sudo bash deployment/systemd/uninstall-system.sh` |
| **Check status** | `systemctl --user status sentinel.service` | `sudo systemctl status sentinel.service` |
| **View logs** | `journalctl --user -u sentinel.service` | `sudo journalctl -u sentinel.service` |
| **Start service** | `systemctl --user start sentinel.service` | `sudo systemctl start sentinel.service` |
| **Stop service** | `systemctl --user stop sentinel.service` | `sudo systemctl stop sentinel.service` |
| **View log file** | `tail ~/Sentinel/logs/*.log` | `sudo tail /var/log/sentinel/sentinel.log` |
| **View projects** | `ls ~/Sentinel/projects/` | `sudo ls /var/lib/sentinel/projects/` |
| **Edit config** | `nano ~/Sentinel/config/sentinel_local.json` | `sudo nano /etc/sentinel/sentinel.json` |

## File Locations Comparison

### User-Level Installation
```
/home/it_admin/
└── Sentinel/
    ├── config/              # Configuration files
    ├── sentinel/            # Python package
    ├── deployment/          # Install scripts
    ├── logs/                # Runtime logs
    ├── projects/            # Build outputs
    └── ...

~/.config/systemd/user/
├── sentinel.service         # Service unit
└── sentinel.timer           # Timer unit

~/.local/bin/
└── sentinel                 # CLI command (if pip installed)
```

### System-Wide Installation
```
/opt/sentinel/               # Application installation
├── config/                  # Default configs (templates)
├── sentinel/                # Python package
├── deployment/
├── .venv/                   # Python virtual environment
└── ...

/etc/sentinel/               # System configuration
├── sentinel.json            # Active config
└── examples/                # Example configs

/var/lib/sentinel/           # Application data
└── projects/                # Build outputs

/var/log/sentinel/           # System logs
├── sentinel.log             # Main log
└── runs/                    # Per-run logs

/etc/systemd/system/
├── sentinel.service         # Service unit
└── sentinel.timer           # Timer unit

/usr/local/bin/
└── sentinel -> /opt/sentinel/.venv/bin/sentinel  # CLI symlink
```

## When to Use Each

### Use User-Level Installation When:
- ✓ You're developing/testing Sentinel
- ✓ You don't have sudo access
- ✓ You want to quickly try Sentinel
- ✓ Running on your personal workstation
- ✓ You need to frequently modify code
- ✓ You only need it to run when you're logged in

### Use System-Wide Installation When:
- ✓ Deploying to production server
- ✓ Need service to run 24/7
- ✓ Want proper security isolation
- ✓ Following Linux FHS best practices
- ✓ Multiple users need access
- ✓ Want service to survive reboots/logouts
- ✓ Need centralized logging

## Security Differences

### User-Level Installation
```bash
# Service runs with YOUR permissions
$ whoami
it_admin

$ python run.py -config config/sentinel_local.json
# Can access all files you can access
# Can write to your home directory
# Inherits all your environment variables
```

### System-Wide Installation
```bash
# Service runs with LIMITED permissions
$ sudo systemctl show sentinel.service -p User
User=sentinel

$ id sentinel
uid=999(sentinel) gid=999(sentinel) groups=999(sentinel)

# Can ONLY access:
# - /opt/sentinel/ (read-only after install)
# - /etc/sentinel/ (read-only configs)
# - /var/lib/sentinel/ (read-write data)
# - /var/log/sentinel/ (read-write logs)

# CANNOT access:
# - /home/* (other user directories)
# - /root/
# - Most of /etc/
# - System files
```

## Migration Path

### From User-Level to System-Wide:

```bash
# 1. Stop and disable user service
systemctl --user stop sentinel.service
systemctl --user disable sentinel.service

# 2. Install system-wide
cd ~/Sentinel
sudo bash deployment/systemd/install-system.sh

# 3. Copy your configs to system location
sudo cp ~/Sentinel/config/sentinel_local.json /etc/sentinel/

# 4. Verify
bash deployment/verify_user.sh
# Should show: ✓ SYSTEM-WIDE INSTALLATION DETECTED

# 5. Check the logs
sudo journalctl -u sentinel.service -n 20
# Should show: Running as user: sentinel
```

### From System-Wide to User-Level:

```bash
# 1. Uninstall system-wide
sudo bash deployment/systemd/uninstall-system.sh

# 2. Install user-level
cd ~/Sentinel
bash deployment/systemd/install.sh

# 3. Verify
bash deployment/verify_user.sh
# Should show: ✓ USER-LEVEL INSTALLATION DETECTED

# 4. Check the logs
journalctl --user -u sentinel.service -n 20
# Should show: Running as user: it_admin
```

## Real-World Examples

### Example 1: Development Workflow (User-Level)

```bash
# Morning: Make changes to code
cd ~/Sentinel
nano sentinel/linting.py

# Test manually
python run.py -config config/example.yaml

# Log shows: Running as user: it_admin (UID: 1001, GID: 1001)
# Files created in ~/Sentinel/projects/

# Evening: Let it run overnight
systemctl --user start sentinel.timer

# Logout - service stops (unless lingering enabled)
```

### Example 2: Production Deployment (System-Wide)

```bash
# One-time setup by sysadmin
sudo bash deployment/systemd/install-system.sh

# Configure for production
sudo nano /etc/sentinel/sentinel.json

# Enable and start
sudo systemctl enable --now sentinel.timer

# Service runs 24/7 as 'sentinel' user
# Survives reboots, user logouts

# Developers can check status (no sudo needed for read-only)
systemctl status sentinel.service

# Sysadmin can view logs
sudo journalctl -u sentinel.service
# Shows: Running as user: sentinel (UID: 999, GID: 999)

# All files owned by sentinel
sudo ls -la /var/lib/sentinel/projects/
# drwxr-xr-x sentinel sentinel ...
```

## Troubleshooting

### "I installed system-wide but it still runs as my user"

Check:
```bash
systemctl --user list-unit-files | grep sentinel
```

If you see sentinel.service listed, you have BOTH installed. Remove user-level:
```bash
systemctl --user stop sentinel.service
systemctl --user disable sentinel.service
rm ~/.config/systemd/user/sentinel.*
systemctl --user daemon-reload
```

### "Logs show 'Permission denied' errors"

**User-level:** Check file ownership in `~/Sentinel/`
```bash
ls -la ~/Sentinel/projects/
# Should be owned by your user
```

**System-wide:** Check sentinel user has access
```bash
sudo ls -la /var/lib/sentinel/
# Should be owned by sentinel:sentinel
```

### "Service won't start after reboot"

**User-level:** Services stop when user logs out. Enable lingering:
```bash
loginctl enable-linger $USER
```

**System-wide:** Check service is enabled:
```bash
sudo systemctl is-enabled sentinel.service
# Should show: enabled
```

## Summary

**To know which user Sentinel is running as, just check the logs:**

```bash
# User-level
tail ~/Sentinel/logs/*.log | head -1
# Shows: Running as user: it_admin (UID: 1001, GID: 1001)

# System-wide
sudo tail /var/log/sentinel/sentinel.log | head -1
# Shows: Running as user: sentinel (UID: 999, GID: 999)
```

**Or run the verification script:**
```bash
bash deployment/verify_user.sh
```

That's it! The script and logs tell you exactly which user is running Sentinel.
