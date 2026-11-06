# Running the auth-gateway behind Traefik (forwardAuth)

**This page is for hand-built Traefik setups** (such as a split
deployment with the apps on one cluster and the backend on another). A wip-deploy install needs none of
it, on Traefik or any other controller: on every target the auth gateway
sits behind `wip-caddy`, which owns the login redirect, and on k8s the
ingress controller only hands requests to `wip-caddy`. See
`docs/deploy/k8s/README.md`, "The ingress controller".

wip-caddy **owns the login redirect**: the gateway answers unauthenticated
requests with `401` + `X-Auth-Redirect`, and Caddy turns that into the
browser's trip to `/auth/login` (`handle_response @401 { redir … }`).

Traefik's `forwardAuth` middleware cannot do that: it passes any non-2xx
response from the auth server to the client **verbatim**. A bare 401 shows
the browser an Unauthorized page and the login flow never starts.

## The switch

Set on the auth-gateway container:

```
AUTH_REDIRECT_MODE=redirect
```

In this mode the gateway answers *browser* requests (`Accept: text/html`)
with a `302` to the **absolute public** login URL (built from
`X-Forwarded-Proto` / `X-Forwarded-Host`, which Traefik sets on the
forwardAuth request). API callers still get the `401` + `X-Auth-Redirect`
contract, so programmatic clients never chase HTML redirects.

The default (`AUTH_REDIRECT_MODE=401`) must stay in place behind Caddy and
nginx-ingress, and so on every wip-deploy install. It is **mandatory** behind nginx: `auth_request` treats any
status other than 2xx/401/403 from the auth subrequest — a 302 included —
as an internal error, and every unauthenticated browser hit becomes a 500.
Don't try to have the gateway sniff the proxy family from headers instead
of setting this variable: every proxy forwards the browser's `Accept`
header to the auth subrequest, so requests look the same in all three
constellations.

An unknown value refuses startup (loudly) rather than silently acting as
`401`.

## Middleware shape

```yaml
http:
  middlewares:
    wip-auth:
      forwardAuth:
        address: "http://wip-auth-gateway:4180/auth/verify"
        authResponseHeaders:
          - X-WIP-User
          - X-WIP-Groups
```

List only these two. The gateway never returns the install's key to an app
upstream, and an app calls WIP with its own key.

Attach `wip-auth` to every auth-protected router. Route `/auth/*` to the
gateway itself **without** the middleware — the gateway is the auth; its
login/callback/logout endpoints must stay reachable unauthenticated.
