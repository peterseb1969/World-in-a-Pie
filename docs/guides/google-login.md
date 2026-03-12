# Adding Google Login to WIP

This guide explains how to configure Dex to allow users to authenticate with their Google accounts instead of (or in addition to) local static users.

## Overview

Dex acts as an identity federation layer. When configured with a Google connector, users can click "Login with Google" and authenticate using their existing Google account. This provides:

- No local password management
- Google handles 2FA/MFA
- Google handles password resets
- Optional: Restrict to specific Google Workspace domain

## Prerequisites

1. A Google account with access to [Google Cloud Console](https://console.cloud.google.com/)
2. WIP deployed with OIDC module enabled
3. A publicly accessible hostname OR ability to add localhost to authorized origins (for local dev)

## Step 1: Create Google OAuth Credentials

### 1.1 Create a Google Cloud Project (if needed)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click the project dropdown (top left) → "New Project"
3. Name it (e.g., "WIP Authentication")
4. Click "Create"

### 1.2 Configure OAuth Consent Screen

1. Navigate to **APIs & Services → OAuth consent screen**
2. Choose User Type:
   - **Internal**: Only users in your Google Workspace org (recommended for orgs)
   - **External**: Any Google account (requires verification for production)
3. Fill in required fields:
   - App name: "World In a Pie" (or your preferred name)
   - User support email: your email
   - Developer contact: your email
4. Click "Save and Continue"
5. Scopes: Add `openid`, `email`, `profile` → "Save and Continue"
6. Test users (External only): Add test emails during development
7. Click "Back to Dashboard"

### 1.3 Create OAuth 2.0 Client ID

1. Navigate to **APIs & Services → Credentials**
2. Click **"+ Create Credentials" → "OAuth client ID"**
3. Application type: **Web application**
4. Name: "WIP Dex" (or your preferred name)
5. **Authorized JavaScript origins**:
   ```
   https://localhost:8443
   https://wip-pi.local:8443
   https://your-domain.com
   ```
6. **Authorized redirect URIs**:
   ```
   https://localhost:8443/dex/callback
   https://wip-pi.local:8443/dex/callback
   https://your-domain.com/dex/callback
   ```
7. Click "Create"
8. **Save the Client ID and Client Secret** - you'll need these

## Step 2: Configure Dex

### 2.1 Update Dex Configuration

Edit `config/dex/config.yaml` to add the Google connector:

```yaml
issuer: https://localhost:8443/dex

storage:
  type: sqlite3
  config:
    file: /var/dex/dex.db

web:
  http: 0.0.0.0:5556

oauth2:
  skipApprovalScreen: true

staticClients:
  - id: wip-console
    name: 'WIP Console'
    redirectURIs:
      - 'https://localhost:8443/callback'
      - 'https://wip-pi.local:8443/callback'
    secret: your-client-secret-here

connectors:
  # Local accounts (keep for offline/fallback use)
  - type: mockPassword
    id: local
    name: "Local Account"
    config:
      username: admin@wip.local
      password: "$2a$10$..."  # bcrypt hash
      userID: admin-001
      email: admin@wip.local
      groups:
        - wip-admins

  # Google connector
  - type: google
    id: google
    name: "Google"
    config:
      clientID: YOUR_GOOGLE_CLIENT_ID
      clientSecret: YOUR_GOOGLE_CLIENT_SECRET
      redirectURI: https://localhost:8443/dex/callback

      # Optional: Restrict to specific Google Workspace domain(s)
      # hostedDomains:
      #   - yourcompany.com
      #   - anotherorg.com
```

### 2.2 Using Environment Variables (Recommended)

For security, use environment variables instead of hardcoding secrets:

```yaml
connectors:
  - type: google
    id: google
    name: "Google"
    config:
      clientID: $GOOGLE_CLIENT_ID
      clientSecret: $GOOGLE_CLIENT_SECRET
      redirectURI: https://localhost:8443/dex/callback
```

Then set in your environment or `.env` file:

```bash
GOOGLE_CLIENT_ID=123456789-abcdef.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxx
```

### 2.3 Group Mapping (Optional)

By default, Google users won't have WIP groups. Options:

**Option A: Default group for all Google users**

Add to Dex config (requires custom claims mapper - not built-in):

```yaml
# This requires additional configuration in wip-console
# to assign default groups to external users
```

**Option B: Map Google Workspace groups**

If using Google Workspace, you can map Google groups to Dex groups:

```yaml
connectors:
  - type: google
    id: google
    name: "Google"
    config:
      clientID: $GOOGLE_CLIENT_ID
      clientSecret: $GOOGLE_CLIENT_SECRET
      redirectURI: https://localhost:8443/dex/callback

      # Enable Google Workspace group fetching
      # Requires Domain-Wide Delegation setup in Google Admin
      serviceAccountFilePath: /etc/dex/google-service-account.json
      adminEmail: admin@yourcompany.com
```

**Option C: Handle in application (Simplest)**

Have WIP services assign a default role to users without groups:

```python
# In wip-auth library
groups = token_claims.get("groups", [])
if not groups:
    groups = ["wip-viewers"]  # Default role for external users
```

## Step 3: Update Redirect URIs

Ensure your Dex callback URL matches what Google expects.

### For localhost development:

```
https://localhost:8443/dex/callback
```

### For Raspberry Pi:

```
https://wip-pi.local:8443/dex/callback
```

### For production domain:

```
https://wip.yourdomain.com/dex/callback
```

**Important:** The redirect URI in Dex config MUST exactly match one of the authorized redirect URIs in Google Cloud Console.

## Step 4: Restart Dex

After updating the configuration:

```bash
# If using setup.sh
./scripts/setup.sh --preset <your-profile>

# Or restart Dex container directly
podman restart wip-dex
```

## Step 5: Test Login

1. Open WIP Console: `https://localhost:8443`
2. Click "Login"
3. You should see two options:
   - "Local Account" (existing static users)
   - "Google"
4. Click "Google"
5. Complete Google OAuth flow
6. You should be redirected back to WIP Console, logged in

## Troubleshooting

### "redirect_uri_mismatch" error

The redirect URI in Dex config doesn't match Google's authorized list.

1. Check exact URI in Dex config (including https, port, path)
2. Verify it's listed in Google Cloud Console → Credentials → Your OAuth Client
3. Wait a few minutes after adding URIs (Google can take time to propagate)

### "Access blocked: This app's request is invalid"

OAuth consent screen not configured properly.

1. Ensure OAuth consent screen is published (not in "Testing" mode for production)
2. For External apps, ensure your email is in the test users list during development

### User logs in but has no permissions

Google users don't have WIP groups by default.

1. Implement default group assignment (see Option C above)
2. Or manually map Google Workspace groups (see Option B)

### "Invalid credentials" after adding Google connector

The connector configuration is invalid.

1. Check Dex logs: `podman logs wip-dex`
2. Verify Client ID and Secret are correct
3. Ensure no extra whitespace in config values

## Security Considerations

### Restrict to your organization

For internal use, always set `hostedDomains` to limit login to your Google Workspace domain:

```yaml
config:
  clientID: ...
  clientSecret: ...
  redirectURI: ...
  hostedDomains:
    - yourcompany.com
```

Without this, ANY Google account can attempt to log in.

### Credential storage

- Never commit `clientSecret` to git
- Use environment variables or secrets management
- Rotate credentials if exposed

### HTTPS required

Google OAuth requires HTTPS redirect URIs in production. Self-signed certificates work for development but may show browser warnings.

## Multiple Connectors

You can have multiple connectors active simultaneously:

```yaml
connectors:
  # Local fallback
  - type: mockPassword
    id: local
    name: "Local Account"
    config: ...

  # Google for most users
  - type: google
    id: google
    name: "Google"
    config: ...

  # GitHub for developers
  - type: github
    id: github
    name: "GitHub"
    config:
      clientID: $GITHUB_CLIENT_ID
      clientSecret: $GITHUB_CLIENT_SECRET
      redirectURI: https://localhost:8443/dex/callback
```

Users see all options on the login screen and choose their preferred method.

## Related Documentation

- [Dex Connectors Reference](https://dexidp.io/docs/connectors/)
- [Google OAuth 2.0 Documentation](https://developers.google.com/identity/protocols/oauth2)
- [WIP Authentication Architecture](../authentication.md)
