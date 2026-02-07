# Encryption at Rest

This guide covers options for encrypting WIP data at rest.

---

## Recommendation: Host-Level Encryption

For most deployments, **host-level full-disk encryption** provides the best balance of security, performance, and simplicity.

### Linux (Raspberry Pi)

Use LUKS (Linux Unified Key Setup):

```bash
# During OS installation, choose encrypted partition
# Or encrypt an existing partition:
sudo cryptsetup luksFormat /dev/sda2
sudo cryptsetup open /dev/sda2 wip-data
sudo mkfs.ext4 /dev/mapper/wip-data
```

Mount the encrypted volume for WIP data:
```bash
sudo mount /dev/mapper/wip-data /path/to/wip/data
```

### macOS

Enable FileVault:
1. System Preferences > Security & Privacy > FileVault
2. Turn On FileVault

All data on the drive, including WIP data, will be encrypted.

### Windows (WSL2)

Enable BitLocker:
1. Control Panel > System and Security > BitLocker Drive Encryption
2. Turn on BitLocker for the system drive

---

## Application-Level Encryption

If host-level encryption isn't possible, consider these alternatives:

### MinIO Server-Side Encryption (SSE)

MinIO supports SSE-S3 (server-managed keys) and SSE-KMS (external key management).

**SSE-S3 (simplest):**
```yaml
# Add to docker-compose module
environment:
  MINIO_KMS_KES_ENDPOINT: ""  # Self-managed
  MINIO_KMS_KES_KEY_NAME: "my-key"
```

See [MinIO Encryption Guide](https://min.io/docs/minio/linux/operations/server-side-encryption.html) for details.

### MongoDB Encryption

MongoDB Community Edition does not support encryption at rest. Options:
1. Use host-level encryption (recommended)
2. Upgrade to MongoDB Enterprise for native encryption
3. Use encrypted filesystem for MongoDB data directory

### PostgreSQL Encryption

PostgreSQL does not include built-in encryption at rest. Options:
1. Use host-level encryption (recommended)
2. Use pgcrypto extension for column-level encryption
3. PostgreSQL Enterprise features (third-party)

---

## Encrypted Backups

Always encrypt backups before storing offsite:

### Using GPG

```bash
# Create encrypted backup
tar czf - data/ | gpg --symmetric --cipher-algo AES256 \
  > wip-backup-$(date +%Y%m%d).tar.gz.gpg

# Restore
gpg --decrypt wip-backup-*.tar.gz.gpg | tar xzf -
```

### Using Age (simpler)

```bash
# Install age: brew install age (Mac) or apt install age (Linux)

# Generate key pair
age-keygen -o key.txt

# Encrypt backup
tar czf - data/ | age -r age1... > backup.tar.gz.age

# Decrypt
age -d -i key.txt backup.tar.gz.age | tar xzf -
```

---

## Key Management Recommendations

### Development / Home Use

Store encryption keys:
- In a password manager
- On a separate device (USB drive stored securely)
- Never in the same location as encrypted data

### Production

Consider:
- Hardware Security Module (HSM)
- Cloud KMS (AWS KMS, Google Cloud KMS, Azure Key Vault)
- HashiCorp Vault

---

## Security Comparison

| Method | Protects Against | Complexity | Performance Impact |
|--------|------------------|------------|-------------------|
| Host-level (LUKS/FileVault) | Physical theft | Low | Minimal |
| MinIO SSE | Unauthorized file access | Medium | Low |
| Encrypted backups | Backup theft | Low | At backup time only |
| Column-level (pgcrypto) | DB access without app | High | Per-query |

---

## Best Practices

1. **Layer encryption**: Host-level + encrypted backups
2. **Key rotation**: Rotate encryption keys annually
3. **Test recovery**: Regularly verify you can decrypt and restore
4. **Audit access**: Log who accesses encrypted data
5. **Secure key storage**: Keys should be as protected as data
