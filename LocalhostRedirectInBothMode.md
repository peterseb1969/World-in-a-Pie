# Localhost Redirect in Remote Mode

> **History:** This feature was originally proposed as a fix for "both" mode. The "both" network mode has since been removed entirely. The localhost redirect is now built into "remote" mode.

## Problem

When `setup.sh` is run with `--network remote`, the Dex OIDC issuer is set to the hostname (e.g., `https://wip-pi.local:8443/dex`). If a user accesses the system via `https://localhost:8443` instead, the OIDC login fails silently because:

1. The console's OIDC authority is configured as a relative path (`/dex`), so the browser constructs it from the current URL: `https://localhost:8443/dex`
2. The OIDC discovery document returns the issuer as `https://wip-pi.local:8443/dex`
3. oidc-client-ts detects a mismatch between the authority (`localhost`) and the issuer (`wip-pi.local`) and rejects the response
4. Login fails with no obvious error message

## Solution

In `remote` mode, `setup.sh` generates a Caddyfile with two site blocks:

1. **Redirect block** for `localhost` and `127.0.0.1` -- sends a 302 temporary redirect to `https://{hostname}:{port}{uri}`
2. **Main site block** for the configured hostname -- serves the console and proxies all services

### Generated Caddyfile (remote mode)

```
# Redirect localhost to hostname (convenience for local access)
# Uses 302 (temporary) so browser doesn't cache if mode changes later
localhost, 127.0.0.1 {
    tls internal
    redir https://wip-pi.local:8443{uri} 302
}

# Main site block
wip-pi.local {
    tls internal

    handle /dex/* {
        reverse_proxy wip-dex:5556
    }
    # ... remaining reverse proxy rules ...
    handle {
        reverse_proxy wip-console-dev:3000
    }
}
```

### Why 302, not 301

A 301 (permanent) redirect is cached aggressively by browsers. If the user later switches to `--network localhost` mode, their browser would still redirect localhost to the old hostname. A 302 (temporary) redirect avoids this caching issue.

### User Experience

| Before | After |
|--------|-------|
| `https://localhost:8443` loads the console but Dex login fails silently | `https://localhost:8443` redirects to `https://wip-pi.local:8443` where login works |

The redirect is transparent -- the browser follows it automatically.

## Files Changed

| File | Change |
|------|--------|
| `scripts/setup.sh` | In remote mode, generates a Caddy redirect block for localhost/127.0.0.1 |

## Not Affected

- **`localhost` mode** -- No redirect needed. Dex issuer is `https://localhost:8443/dex`, everything matches.
