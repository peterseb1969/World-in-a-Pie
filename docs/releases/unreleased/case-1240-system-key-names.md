---
section: fixes
cases: [1240]
---

### A spec key cannot take a key name the deployer provisions, and a refused key file is reported

A spec key named `wip-app-edge`, on an install running the app edge, put two keys of that name into the key file. The Registry refuses such a file whole: no key added, removed or narrowed in it took effect, and every apply still reported success.

- Every name the deployer provisions (`wip-canary`, `wip-app-edge`) is now reserved.
- The render refuses a key file that would name any key twice.
- The Registry's health answer carries `key_file_refused`, and `wip-deploy verify --security --live` fails when it is true. The Registry's log names why.
