---
section: fixes
cases: [1086]
packages: [wip-auth]
---

### A key check with no room is refused at once, instead of joining a queue that never clears

Two bcrypt threads per service bounded how many key checks ran, but not how many requests waited for one. A client sending a fresh random `X-API-Key` on every request misses the caches every time, so all of its requests got in line: the backlog grew for as long as arrivals beat the scan rate (every key the service holds, ~170 ms each), and a real key's first check waited behind that flood with no limit and no answer.

A service now lets at most eight checks wait behind its two running ones, and holds a waiter for at most five seconds. Past either bound the request is refused at once with **503** and runs no bcrypt — so a flood cannot fund its own queue. A refusal caches nothing about the key: the same value is judged from scratch once there is room again, never recorded as a wrong key.

The checks also moved onto their own two threads. On the event loop's shared executor, a check waited behind unrelated blocking work while holding its place in line.
