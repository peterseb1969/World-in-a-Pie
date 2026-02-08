# Containers Die After SSH Disconnect or Logout

## Symptom

All WIP containers stop running after:
- SSH session disconnects
- User logs out
- Client machine (e.g., Mac) goes to sleep

Containers show various exit codes:
- **Exit 0**: Clean shutdown (MongoDB, PostgreSQL, Caddy, etc.)
- **Exit 137**: SIGKILL - container didn't stop in time (Python services)
- **Exit 1**: Error during shutdown

No OOM errors in `dmesg`, no system reboot, plenty of memory and disk space.

## Root Cause

**Rootless Podman containers run in your user session.** When you log out (or all SSH sessions close), systemd kills all user processes - including your containers.

This is controlled by the `Linger` setting:

```bash
loginctl show-user $USER | grep Linger
# Linger=no  <- This is the problem
```

## Solution

Enable lingering for your user:

```bash
sudo loginctl enable-linger $USER
```

Verify:

```bash
loginctl show-user $USER | grep Linger
# Linger=yes
```

This is a **one-time fix** that persists across reboots. Your containers will now run 24/7 regardless of SSH sessions.

## Container Runtime Comparison

### Why Docker Doesn't Have This Problem

Standard Docker runs as a **system daemon** (`dockerd`) under root. Containers are managed by the daemon, not user sessions. The daemon runs as a systemd system service, independent of user logins.

### Podman: Rootless vs Rootful

Podman supports two modes:

| Mode | How to run | Linger needed | UID mapping issues | Security |
|------|------------|---------------|-------------------|----------|
| **Rootless** (default) | `podman ...` | Yes | Yes (e.g., Dex chown) | Best (user namespace isolation) |
| **Rootful** | `sudo podman ...` | No | No | Standard (runs as root) |

**Rootful Podman** behaves like Docker:
- Containers managed by system-level systemd services
- Data stored in `/var/lib/containers/` instead of `~/.local/share/containers/`
- No linger configuration needed
- No user namespace UID mapping headaches

To run rootful: prefix all `podman` and `podman-compose` commands with `sudo`.

### Full Comparison

| | Rootless Podman | Rootful Podman | Docker |
|---|---|---|---|
| Linger required | Yes | No | No |
| UID mapping issues | Yes | No | No |
| Runs as root | No | Yes | Yes |
| Security isolation | Best | Standard | Standard |
| Daemon required | No | No | Yes |

### Which Should I Use?

- **Rootless Podman** (WIP default): Best security, but requires linger. Good for multi-user or exposed systems.
- **Rootful Podman**: Simpler setup, no linger/UID issues. Good for dedicated appliances like a Pi.
- **Docker**: Industry standard, well-documented. Requires daemon. Good if you're already familiar with Docker.

For a Raspberry Pi running as a dedicated WIP server, rootful Podman or Docker would be simpler to manage. The security benefit of rootless matters more in multi-tenant or exposed environments.

## Prevention

The WIP setup script should enable linger automatically. If you're setting up a new Pi, the script will handle this.

## Related

- [systemd user sessions](https://wiki.archlinux.org/title/Systemd/User)
- [Podman rootless networking](https://github.com/containers/podman/blob/main/rootless.md)
