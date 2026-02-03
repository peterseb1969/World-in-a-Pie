# Localhost Redirect in Both Mode

## Problem

When `setup.sh` is run with `--network both`, the Dex OIDC issuer is set to the hostname (e.g., `https://wip-pi.local:8443/dex`) because Dex can only have a single issuer URL. Previously, Caddy served both `localhost` and the hostname identically, so users could open `https://localhost:8443` and see the WIP Console. However, clicking "Login with Dex" would fail silently because:

1. The console's OIDC authority is configured as a relative path (`/dex`), so the browser constructs it from the current URL: `https://localhost:8443/dex`
2. The OIDC discovery document at that URL returns the issuer as `https://wip-pi.local:8443/dex`
3. oidc-client-ts detects a mismatch between the authority (`localhost`) and the issuer (`wip-pi.local`) and rejects the response
4. Login fails with no obvious error message

## Solution

In `both` mode, `setup.sh` now generates a Caddyfile with two site blocks:

1. **Redirect block** for `localhost` and `127.0.0.1` -- sends a 301 permanent redirect to `https://{hostname}:{port}{uri}`
2. **Main site block** for `*.local`, `192.168.*.*`, and the configured hostname -- serves the console and proxies all services

### Generated Caddyfile (both mode)

```
# Redirect localhost to hostname (required for OIDC issuer match)
localhost, 127.0.0.1 {
    tls internal
    redir https://wip-pi.local:8443{uri} permanent
}

# Main site block
*.local, 192.168.*.*, wip-pi.local {
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

### User Experience

| Before | After |
|--------|-------|
| `https://localhost:8443` loads the console but Dex login fails silently | `https://localhost:8443` redirects to `https://wip-pi.local:8443` where login works |

The redirect is transparent -- the browser follows it automatically. Since `hostname.local` resolves to the same machine on the local network, there is no difference in connectivity.

## Files Changed

| File | Change |
|------|--------|
| `scripts/setup.sh` | Removed `localhost` and `127.0.0.1` from the main Caddy hosts in `both` mode. Added a redirect block that sends localhost traffic to the configured hostname. |

## Not Affected

- **`localhost` mode** -- No redirect, Dex issuer is `https://localhost:8443/dex`, everything matches.
- **`remote` mode** -- No localhost access at all, only the hostname is served.
