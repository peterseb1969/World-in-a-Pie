# WIP Storage Configuration

This document explains how to configure storage for WIP deployments.

## Overview

WIP stores data for several services:

| Service | Data Type | Default Location |
|---------|-----------|------------------|
| MongoDB | Documents, templates, terminologies | `./data/mongodb` |
| PostgreSQL | Reporting data (SQL tables) | `./data/postgres` |
| NATS | Message queue persistence (JetStream) | `./data/nats` |
| MinIO | Binary file storage (S3-compatible) | `./data/minio` |
| Dex | OIDC tokens and sessions | `./data/dex` |
| Caddy | TLS certificates and config | `./data/caddy` |

## Quick Start

By default, data is stored in `./data/` relative to the project root. To use a different location:

```bash
# Set storage location before running setup
export WIP_DATA_DIR=/path/to/storage

# Run setup script (same on all platforms)
./scripts/setup.sh
```

## Storage Options

### Local Storage (Default)

Uses a directory on the local filesystem. Good for development and simple deployments.

```bash
# Default (./data in project directory)
./scripts/setup.sh

# Custom local path
WIP_DATA_DIR=/opt/wip-data ./scripts/setup.sh
```

### USB SSD (Raspberry Pi)

Recommended for Pi deployments to avoid SD card wear and improve performance.

```bash
# 1. Connect USB SSD and identify device
lsblk

# 2. Create filesystem (if new drive) - CAUTION: destroys data
sudo mkfs.ext4 /dev/sda1

# 3. Create mount point
sudo mkdir -p /mnt/wip-data

# 4. Mount the drive
sudo mount /dev/sda1 /mnt/wip-data

# 5. Set ownership (for rootless Podman)
sudo chown -R $USER:$USER /mnt/wip-data

# 6. Run setup with external storage
WIP_DATA_DIR=/mnt/wip-data ./scripts/setup.sh
```

**Auto-mount on boot:**

Add to `/etc/fstab`:
```
/dev/sda1  /mnt/wip-data  ext4  defaults,noatime  0  2
```

Or by UUID (more reliable):
```bash
# Get UUID
sudo blkid /dev/sda1

# Add to /etc/fstab
UUID=your-uuid-here  /mnt/wip-data  ext4  defaults,noatime  0  2
```

### NFS (Network Storage)

Share storage across multiple machines or use a NAS.

```bash
# 1. Install NFS client
sudo apt install nfs-common  # Debian/Ubuntu/Pi OS

# 2. Create mount point
sudo mkdir -p /mnt/wip-data

# 3. Mount NFS share
sudo mount -t nfs nas.local:/exports/wip-data /mnt/wip-data

# 4. Set ownership
sudo chown -R $USER:$USER /mnt/wip-data

# 5. Run setup
WIP_DATA_DIR=/mnt/wip-data ./scripts/setup.sh
```

**Auto-mount on boot:**

Add to `/etc/fstab`:
```
nas.local:/exports/wip-data  /mnt/wip-data  nfs  defaults,_netdev  0  0
```

**NFS Server Setup (on NAS/server):**

```bash
# Install NFS server
sudo apt install nfs-kernel-server

# Create export directory
sudo mkdir -p /exports/wip-data
sudo chown nobody:nogroup /exports/wip-data

# Configure export (edit /etc/exports)
/exports/wip-data  192.168.1.0/24(rw,sync,no_subtree_check,no_root_squash)

# Apply changes
sudo exportfs -ra
sudo systemctl restart nfs-kernel-server
```

### GlusterFS (Distributed Storage)

For high availability across multiple nodes.

```bash
# 1. Install GlusterFS client
sudo apt install glusterfs-client

# 2. Create mount point
sudo mkdir -p /mnt/wip-data

# 3. Mount GlusterFS volume
sudo mount -t glusterfs node1.local:/wip-volume /mnt/wip-data

# 4. Run setup
WIP_DATA_DIR=/mnt/wip-data ./scripts/setup.sh
```

**Auto-mount on boot:**

Add to `/etc/fstab`:
```
node1.local:/wip-volume  /mnt/wip-data  glusterfs  defaults,_netdev  0  0
```

### Ceph (Enterprise Distributed Storage)

For large-scale deployments with high availability requirements.

```bash
# 1. Install Ceph client
sudo apt install ceph-common

# 2. Mount CephFS
sudo mount -t ceph mon1:6789:/ /mnt/wip-data -o name=admin,secret=<key>

# Or use ceph-fuse for userspace mount
sudo ceph-fuse /mnt/wip-data

# 3. Run setup
WIP_DATA_DIR=/mnt/wip-data ./scripts/setup.sh
```

## Directory Structure

When WIP starts, it creates this structure in `WIP_DATA_DIR`:

```
$WIP_DATA_DIR/
├── mongodb/          # MongoDB data files
├── postgres/         # PostgreSQL data files
├── nats/             # NATS JetStream data
├── minio/            # MinIO file storage (if files module enabled)
├── dex/              # Dex SQLite database
└── caddy/
    ├── data/         # Caddy TLS certificates
    └── config/       # Caddy runtime config
```

## Permissions

For rootless Podman, the storage directory must be owned by your user:

```bash
sudo chown -R $USER:$USER /path/to/storage
```

For external mounts (USB, NFS), ensure the mount has correct permissions or use `no_root_squash` for NFS.

### Container User Mapping

Some containers run as non-root users inside the container. With rootless Podman, these UIDs are mapped through the user namespace. The setup scripts handle this automatically, but if you're setting up storage manually:

**Dex (OIDC provider)** runs as UID 1001 inside the container:
```bash
# Use podman unshare to set ownership within Podman's user namespace
podman unshare chown 1001:1001 /path/to/storage/dex
```

This maps to a high UID on the host (e.g., 101000) but appears as UID 1001 inside the container.

**MongoDB and PostgreSQL** create their own data directories with correct permissions, so no special handling is needed.

**Why not chmod 777?** Never use 777 for directories containing authentication data. The `podman unshare chown` approach maintains proper security by giving only the specific container user access.

## Backup

### Quick Backup

Stop services and copy data:

```bash
# Stop all containers
podman stop -a

# Backup data directory
tar -czvf wip-backup-$(date +%Y%m%d).tar.gz $WIP_DATA_DIR

# Restart services
./scripts/setup.sh
```

### MongoDB Backup (Hot)

Backup without stopping:

```bash
# Dump all databases
podman exec wip-mongodb mongodump --out /data/db/backup

# Copy from container
cp -r $WIP_DATA_DIR/mongodb/backup ./mongodb-backup-$(date +%Y%m%d)
```

### PostgreSQL Backup (Hot)

```bash
# Dump reporting database
podman exec wip-postgres pg_dump -U wip wip_reporting > wip-reporting-$(date +%Y%m%d).sql
```

## Migration

To move data to a new location:

```bash
# 1. Stop all containers
podman stop -a

# 2. Copy data to new location
rsync -av $WIP_DATA_DIR/ /new/location/

# 3. Update WIP_DATA_DIR and restart
export WIP_DATA_DIR=/new/location
./scripts/setup.sh
```

## Performance Considerations

| Storage Type | Read Speed | Write Speed | Best For |
|--------------|------------|-------------|----------|
| SD Card | Slow | Very Slow | Not recommended for data |
| USB SSD | Fast | Fast | Pi deployments |
| NFS | Medium | Medium | Shared storage, backups |
| GlusterFS | Medium | Medium | Multi-node HA |
| Local SSD | Very Fast | Very Fast | Mac/Desktop development |

**Recommendations:**
- **Pi**: Always use USB SSD for data, keep SD card for OS only
- **Development**: Local SSD is fine
- **Production**: NFS or distributed FS for redundancy

## Troubleshooting

### Permission Denied

```bash
# Check ownership
ls -la $WIP_DATA_DIR

# Fix ownership
sudo chown -R $USER:$USER $WIP_DATA_DIR
```

### MongoDB Won't Start

Check if data directory is on a filesystem MongoDB supports:

```bash
# MongoDB doesn't support some network filesystems for WiredTiger
# Use directoryPerDB option or switch to mmapv1 for NFS
```

### NFS Mount Fails on Boot

Ensure network is up before mounting:

```bash
# Use _netdev option in fstab
nas.local:/exports/wip  /mnt/wip-data  nfs  defaults,_netdev  0  0
```

### Dex Won't Start (Database Error)

If Dex logs show:
```
failed to initialize storage: unable to open database file: no such file or directory
```

**Fix:** Set correct ownership for the Dex directory:
```bash
podman unshare chown 1001:1001 $WIP_DATA_DIR/dex
podman restart wip-dex
```

### Disk Full

Check disk usage:

```bash
du -sh $WIP_DATA_DIR/*

# Output example:
# 2.1G    mongodb
# 500M    postgres
# 100M    nats
# 10M     dex
# 5M      caddy
```
