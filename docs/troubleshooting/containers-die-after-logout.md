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

## Why Docker Doesn't Have This Problem

Standard Docker runs as a **system daemon** (`dockerd`) under root. Containers are managed by the daemon, not user sessions. The daemon runs as a systemd system service, independent of user logins.

**Rootless Docker** would have the same linger issue as rootless Podman.

## Prevention

The WIP setup script should enable linger automatically. If you're setting up a new Pi, the script will handle this.

## Related

- [systemd user sessions](https://wiki.archlinux.org/title/Systemd/User)
- [Podman rootless networking](https://github.com/containers/podman/blob/main/rootless.md)
