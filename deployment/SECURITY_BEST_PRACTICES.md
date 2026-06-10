# Security Best Practices for Sentinel Deployment

## Overview
Running Sentinel as a system service with a dedicated user account provides multiple layers of security. This document explains the security model, why it's recommended, and how to verify it's working correctly.

---

## Why Use a Dedicated System User?

### ✅ Security Benefits

1. **Privilege Isolation**
   - If Sentinel is compromised, attacker only has `sentinel` user privileges
   - Cannot access other users' home directories
   - Cannot modify system files (/usr, /boot, /etc)
   - Cannot install packages or create new users

2. **Blast Radius Containment**
   - Damage limited to /var/lib/sentinel and /var/log/sentinel
   - Application code in /opt/sentinel is read-only (owned by root)
   - Configuration in /etc/sentinel is read-only (owned by root)

3. **Audit Trail**
   - All file modifications traceable to `sentinel` user
   - Process ownership clearly identified: `ps aux | grep sentinel`
   - systemd journal entries tagged with service name

4. **Defense in Depth**
   - Multiple security layers (user isolation + systemd hardening + filesystem permissions)
   - Even if one layer fails, others protect the system

### ✅ Operational Benefits

1. **Clear Resource Ownership**
   ```bash
   ls -la /opt/sentinel       # root:root (immutable)
   ls -la /var/lib/sentinel   # sentinel:sentinel (writable)
   ls -la /var/log/sentinel   # sentinel:sentinel (writable)
   ```

2. **Process Management**
   ```bash
   ps aux | grep sentinel     # Easy to identify service processes
   pgrep -u sentinel          # List all sentinel user processes
   ```

3. **Professional Standard**
   - Apache runs as `www-data`
   - MySQL runs as `mysql`
   - PostgreSQL runs as `postgres`
   - Sentinel runs as `sentinel` ✅

---

## Current Security Implementation

### 1. Dedicated System User

```bash
# Created by install-system.sh
useradd --system \
        --no-create-home \
        --shell /bin/false \
        --comment "Sentinel FPGA Build Service" \
        sentinel
```

**Characteristics:**
- `--system`: UID < 1000, not shown in login screens
- `--no-create-home`: No /home/sentinel directory
- `--shell /bin/false`: Cannot login interactively
- No password: Cannot authenticate via SSH/console
- Not in sudoers: Cannot escalate privileges

**How to verify:**
```bash
id sentinel                  # Should show system UID (< 1000)
grep sentinel /etc/passwd    # Shell should be /bin/false
su - sentinel                # Should fail with "This account is not available"
```

### 2. Filesystem Permissions

```
Location                  Owner           Permissions  Purpose
─────────────────────────────────────────────────────────────────────
/opt/sentinel/            root:root       755 (rx)     Application (read-only)
/opt/sentinel/venv/       root:root       755 (rx)     Python environment
/etc/sentinel/            root:root       755 (rx)     Configuration (read-only)
/etc/sentinel/*.json      root:root       640 (r--)    Config files
/var/lib/sentinel/        sentinel:sentinel 755 (rwx) Data directory (writable)
/var/lib/sentinel/projects/ sentinel:sentinel 755 (rwx) Build outputs
/var/log/sentinel/        sentinel:sentinel 755 (rwx) Logs (writable)
```

**Security Model:**
- **Root owns application** → sentinel cannot modify its own code
- **Root owns config** → sentinel cannot change its own configuration
- **Sentinel owns data** → can write build outputs and logs
- **Minimal write access** → only /var/lib/sentinel and /var/log/sentinel

**How to verify:**
```bash
ls -la /opt/sentinel                    # Should show root:root
ls -la /etc/sentinel                    # Should show root:root
ls -la /var/lib/sentinel                # Should show sentinel:sentinel
sudo -u sentinel touch /opt/sentinel/test  # Should FAIL (permission denied)
sudo -u sentinel touch /var/lib/sentinel/test  # Should SUCCEED
```

### 3. systemd Security Hardening

The `sentinel.service` file includes multiple security features:

```ini
[Service]
User=sentinel                            # Run as non-root user
Group=sentinel

# Privilege restrictions
NoNewPrivileges=true                     # Cannot escalate privileges (no sudo/setuid)
PrivateDevices=true                      # Cannot access /dev/* devices
ProtectKernelTunables=true               # Cannot modify /proc/sys/*
ProtectControlGroups=true                # Cannot modify cgroup settings

# Filesystem restrictions
ProtectSystem=strict                     # /usr, /boot, /efi read-only
ProtectHome=read-only                    # /home/* inaccessible
ReadWritePaths=/var/lib/sentinel /var/log/sentinel  # Only these writable
PrivateTmp=true                          # Isolated /tmp namespace

# Network restrictions
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6  # Only Unix/IPv4/IPv6 sockets

# Resource limits
MemoryLimit=2G                           # Maximum 2GB RAM
CPUQuota=80%                             # Maximum 80% CPU
```

**What each directive does:**

- **NoNewPrivileges**: Prevents `sudo`, `setuid` binaries, or capability escalation
- **ProtectSystem=strict**: Makes /, /usr, /boot, /efi read-only
- **ProtectHome**: Blocks access to /home/* directories
- **ReadWritePaths**: Whitelist of directories Sentinel can write to
- **PrivateTmp**: Service gets its own /tmp, isolated from other processes
- **PrivateDevices**: Blocks access to hardware devices in /dev
- **RestrictAddressFamilies**: Limits network protocols (prevents exotic exploits)
- **MemoryLimit/CPUQuota**: Prevents resource exhaustion attacks

**How to verify:**
```bash
systemctl show sentinel.service | grep -E '(User|ProtectSystem|NoNew)'
systemd-analyze security sentinel.service  # Security score (lower is better)
```

### 4. Log Rotation (Prevents Disk Exhaustion)

```bash
# /etc/logrotate.d/sentinel
/var/log/sentinel/*.log {
    daily                    # Rotate daily
    rotate 14                # Keep 14 days
    compress                 # Compress old logs (gzip)
    delaycompress           # Keep most recent uncompressed
    create 0640 sentinel sentinel  # New logs owned by sentinel
}
```

**Prevents:**
- Disk space exhaustion from unbounded log growth
- Denial of service via log flooding
- Performance degradation from huge log files

**How to verify:**
```bash
cat /etc/logrotate.d/sentinel
logrotate -d /etc/logrotate.d/sentinel   # Dry run test
```

---

## Comparison: System User vs Personal User

| Aspect | Personal User (~/Sentinel) | System User (/opt/sentinel) |
|--------|----------------------------|------------------------------|
| **User Account** | it_admin, user1, etc. | sentinel (dedicated) |
| **Can Login?** | ✅ Yes (SSH, console) | ❌ No (shell=/bin/false) |
| **Home Directory** | /home/user | None (uses /opt) |
| **sudo Access** | ⚠️ Often yes | ❌ Never |
| **Code Ownership** | user:user (can modify) | root:root (immutable) |
| **Config Ownership** | user:user (can modify) | root:root (immutable) |
| **systemd Hardening** | ⚠️ Difficult | ✅ Full support |
| **Audit Trail** | ⚠️ Mixed with user activity | ✅ Clean, service-only |
| **Security Risk** | ⚠️ Higher (privilege escalation) | ✅ Lower (isolated) |
| **Professional Standard** | ❌ Development only | ✅ Production best practice |

---

## Attack Scenarios & Mitigations

### Scenario 1: Code Injection in VHDL File
**Attack**: Malicious VHDL file contains exploit in comments, parsed by linter
**Without dedicated user**: Exploit runs as your user, can read SSH keys, modify source code
**With dedicated user**: ✅ Exploit runs as `sentinel`, cannot access /home, cannot modify /opt

### Scenario 2: Command Injection via Git URL
**Attack**: Malicious git URL like `https://evil.com/repo.git; rm -rf /`
**Without dedicated user**: Could delete files in your home directory
**With dedicated user**: ✅ ProtectHome=read-only prevents /home access, ReadWritePaths limits damage

### Scenario 3: Privilege Escalation via setuid Binary
**Attack**: Exploit finds setuid binary vulnerability (e.g., old sudo bug)
**Without dedicated user**: Could escalate to root
**With dedicated user**: ✅ NoNewPrivileges=true blocks setuid execution

### Scenario 4: Memory Exhaustion (Denial of Service)
**Attack**: Malformed VHDL file causes infinite loop, consumes all RAM
**Without dedicated user**: System becomes unresponsive, requires hard reboot
**With dedicated user**: ✅ MemoryLimit=2G kills process, system stays responsive

### Scenario 5: Log Flooding
**Attack**: Malicious input causes excessive logging, fills disk
**Without dedicated user**: Disk full, system crashes
**With dedicated user**: ✅ Logrotate prevents unbounded growth, automatically compresses/deletes old logs

---

## Verification Checklist

After installation, verify security configuration:

```bash
# 1. Check user exists and is properly configured
id sentinel
# Expected: uid=<number>(sentinel) gid=<number>(sentinel) groups=<number>(sentinel)

grep sentinel /etc/passwd
# Expected: sentinel:x:<uid>:<gid>:Sentinel FPGA Build Service:/nonexistent:/bin/false

# 2. Check filesystem ownership
ls -la /opt/sentinel | head -5
# Expected: drwxr-xr-x ... root root ...

ls -la /var/lib/sentinel | head -5
# Expected: drwxr-xr-x ... sentinel sentinel ...

# 3. Check systemd configuration
systemctl cat sentinel.service | grep -E '(User=|NoNew|Protect)'
# Expected: User=sentinel, NoNewPrivileges=true, ProtectSystem=strict, etc.

# 4. Test write restrictions
sudo -u sentinel touch /opt/sentinel/test
# Expected: Permission denied ✅

sudo -u sentinel touch /var/lib/sentinel/test
# Expected: Success ✅
sudo -u sentinel rm /var/lib/sentinel/test

# 5. Check security score
systemd-analyze security sentinel.service
# Expected: Overall exposure level: 2.3 MEDIUM (lower is better)

# 6. Test login restrictions
su - sentinel
# Expected: This account is currently not available ✅

# 7. Verify logrotate
cat /etc/logrotate.d/sentinel
# Expected: Configuration file exists ✅
```

---

## Additional Hardening (Optional)

### 1. SELinux/AppArmor Policies
For high-security environments, create custom MAC (Mandatory Access Control) policies:

**SELinux** (Red Hat, CentOS, Fedora):
```bash
# Create custom policy for Sentinel
semanage fcontext -a -t sentinel_exec_t /opt/sentinel/venv/bin/python
restorecon -Rv /opt/sentinel
```

**AppArmor** (Ubuntu, Debian):
```bash
# Create /etc/apparmor.d/sentinel profile
aa-genprof /opt/sentinel/venv/bin/python
```

### 2. Restrict Network Access
If Sentinel doesn't need internet after installation:

```ini
# Add to sentinel.service
[Service]
IPAddressDeny=any
IPAddressAllow=localhost
```

### 3. Enable Audit Logging
Track all sentinel user activity:

```bash
# Add audit rule
auditctl -w /var/lib/sentinel -p wa -k sentinel_data
auditctl -w /opt/sentinel -p wa -k sentinel_code

# View audit logs
ausearch -k sentinel_data
```

### 4. Immutable Configuration
Prevent even root from accidentally modifying config:

```bash
# Make config immutable (requires root + chattr -i to modify)
chattr +i /etc/sentinel/config.json

# Remove immutability when needed
chattr -i /etc/sentinel/config.json
```

### 5. Restrict CPU Affinity
Pin Sentinel to specific CPU cores to prevent interference with other services:

```ini
# Add to sentinel.service
[Service]
CPUAffinity=0-3    # Use only cores 0-3
```

---

## Monitoring & Alerting

### Real-time Monitoring

```bash
# Watch Sentinel processes
watch -n 1 'ps aux | grep sentinel'

# Monitor resource usage
systemctl status sentinel.service

# Live journal logs
journalctl -u sentinel.service -f
```

### Set Up Alerts

**Disk Space Monitoring:**
```bash
# Alert if /var/lib/sentinel exceeds 10GB
du -sh /var/lib/sentinel | awk '{if ($1 > "10G") print "WARNING: Sentinel data exceeds 10GB"}'
```

**Failed Login Attempts:**
```bash
# Monitor for suspicious su attempts to sentinel user
grep "sentinel" /var/log/auth.log | grep "FAILED"
```

**Service Failures:**
```bash
# Email alert on service failure (configure in systemd or monitoring tool)
OnFailure=status-email@%i.service
```

---

## Incident Response

If you suspect Sentinel has been compromised:

```bash
# 1. Stop the service immediately
sudo systemctl stop sentinel.service

# 2. Check for suspicious processes
ps aux | grep sentinel

# 3. Check for unauthorized file modifications
find /opt/sentinel -mtime -1 -ls   # Files modified in last 24 hours
find /var/lib/sentinel -mtime -1 -ls

# 4. Review logs for suspicious activity
journalctl -u sentinel.service --since "1 hour ago" | grep -i "error\|fail\|warning"

# 5. Check network connections
ss -tunap | grep sentinel   # Should be none if service stopped

# 6. Restore from backup
rm -rf /var/lib/sentinel/*
tar -xzf /backups/sentinel-data-YYYYMMDD.tar.gz -C /var/lib/sentinel

# 7. Investigate root cause before restarting
```

---

## Summary: Key Takeaways

✅ **Dedicated system user is mandatory for production** - Not optional, industry standard  
✅ **Defense in Depth** - User isolation + systemd hardening + filesystem permissions  
✅ **Immutable application** - Root owns code/config, sentinel cannot modify itself  
✅ **Minimal privileges** - Only write access to data/logs, everything else read-only  
✅ **Resource limits** - Prevents DoS via memory/CPU exhaustion  
✅ **Audit trail** - All actions traceable, easy to monitor  
✅ **Attack surface reduction** - No login, no sudo, no setuid, limited network  

**Bottom Line:** The dedicated `sentinel` user approach transforms Sentinel from a user script into a hardened production service that follows Linux security best practices and industry standards.

---

## References

- [systemd Service Hardening](https://www.freedesktop.org/software/systemd/man/systemd.exec.html#Security)
- [Filesystem Hierarchy Standard (FHS)](https://refspecs.linuxfoundation.org/FHS_3.0/fhs-3.0.pdf)
- [NIST Application Container Security Guide](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-190.pdf)
- [CIS Benchmark for Linux](https://www.cisecurity.org/benchmark/distribution_independent_linux)
- [systemd-analyze security](https://www.freedesktop.org/software/systemd/man/systemd-analyze.html#security%20%5BUNIT...%5D)
