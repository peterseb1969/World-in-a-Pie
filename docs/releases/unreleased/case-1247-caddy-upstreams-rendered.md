---
section: fixes
cases: [1247]
---

### A render that routes to a service it did not produce is refused

Caddy accepts a `reverse_proxy` to a host that does not exist, and fails only when a request arrives (a 502). A route whose condition drifted from its service's activation could therefore ship without any signal. One such case was a gateway-off install routing app origins to the auth gateway, fixed earlier.

Every render now checks that each `reverse_proxy`, `forward_auth` and `ask` upstream in its Caddyfiles names a service the same render produced. On k8s those Caddyfiles live in the front door's and the router's ConfigMaps. Otherwise the render stops, naming the file, the site, the directive and the host.

The check covers hosts only; a wrong port is not caught.
