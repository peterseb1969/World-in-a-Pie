# YAC Coding Manifest

Rules for every coding YAC (BE-YAC and APP-YAC). These are non-negotiable — they encode corrections Peter has made repeatedly. If you find yourself about to violate one, stop and re-read it.

---

## 1. Bug Report Before Workaround

If you discover a bug or gap in a library, API, or tool:

1. **File a CASE first.** Describe the bug, expected behavior, and how you found it.
2. **Only then** implement a temporary workaround if you're blocked.
3. **Never** silently work around a gap and call it "not worth a case."

Every gap filed is a gap fixed for the next agent. Every gap silently worked around is a gap the next agent hits again.

## 2. Think Before You Code

Before implementing non-trivial changes:

1. **Describe your plan** in plain language. What will change, why, and what could go wrong.
2. **Wait for confirmation** on anything that touches safety, security, or shared interfaces.
3. **Do not** start coding while discussing design. Peter will tell you when to implement.

If Peter says "DO NOT CODE yet" — stop. Think. Answer his questions. The plan always improves.

## 3. Verify Before You Assume

- **Always curl the actual endpoint** before writing TypeScript types for it. Response shapes from docs or memory may be wrong.
- **Always check your library versions** before filing a case about missing features. They may already exist in a newer version.
- **Always check case history** before filing. Your question may already be answered.

The previous YAC guessed. You won't.

## 4. No `as any`

If TypeScript types don't match what the API accepts:

1. That's a bug. File a CASE (see Rule 1).
2. **Never** use `as any` to bypass type restrictions on API calls. `as any` defeats the entire client-side type safety layer.
3. If you must cast temporarily while waiting for a fix, use a narrow intersection type (`as ItemType & { field: string }`), never `as any`.

Incomplete types push developers toward `as any`, which is worse than no types at all. With no types, you read the docs. With `as any`, you think you have safety but don't.

## 5. Strict Parameter Discipline

- **Request types must match the API.** If the API accepts a field, the client type must include it. If it doesn't, that's API-client type drift — file a CASE.
- **Response types must match the API.** If the API returns a field, the client type must expose it. Missing response fields cause silent data loss.
- **Never send untyped data.** If you can't express it in the type system, the type system needs fixing — not bypassing.

The server has `StrictModel(extra='forbid')`. The client should be equally strict.

## 6. File Every Gap

When you encounter a gap between what the platform provides and what you need:

- Missing type field → CASE
- Missing API endpoint → CASE
- Wrong response shape → CASE
- Library bug (e.g., option spread order) → CASE
- Missing hook or utility → CASE (but check versions first — Rule 3)

The cost of a redundant case is one response. The cost of an unfiled gap is every future agent hitting the same wall.

## 7. Don't Bypass Safety Mechanisms

If a feature exists for safety (deletion_mode, confirmation gates, namespace scoping):

- **Never silently override it.** Setting `deletion_mode='full'` before delete without user knowledge is bypassing a safety mechanism.
- **Make safety visible.** Show the user what protection exists and let them explicitly choose to proceed.
- **Two-step confirmation** for destructive operations on protected resources.

## 8. Cross-Agent Communication

When another YAC's work affects yours:

- **Read the case response fully** before acting on it. Don't skim.
- **Verify with curl** that fixes are actually deployed, not just committed. Containers may be stale.
- **Update the case** with your findings — success or failure. The case is the institutional memory.

Trust but verify. Even when another YAC says a fix is live, curl the actual endpoint.

## 9. Encode What You Learn

When Peter corrects your approach:

1. Ask yourself: "Would the next YAC make the same mistake?"
2. If yes, write it down: `/lesson`, dead ends section, or CLAUDE.md update suggestion.
3. "Got it, won't happen again" is meaningless — **you** won't exist next session. The lesson must be in a file.

## 10. Speed Is Secondary

Peter's directive:

> "Be thorough, and think deep, do not cut corners. Speed is secondary, this has to be rock solid and powerful."

- A correct implementation that took an hour beats a fast one that needs three rounds of fixes.
- A well-filed bug report is more valuable than a working workaround.
- "We don't need a working app. We need bug reports."

---

## Quick Reference

| Situation | Wrong | Right |
|-----------|-------|-------|
| Library type missing a field | `as any` | File CASE, use narrow cast if blocked |
| API returns unexpected shape | Adjust your code to match | Curl first, file CASE if type is wrong |
| Feature seems missing from library | Implement it yourself | Check latest version, then file CASE |
| Safety mechanism blocks you | Override it silently | Make it visible, add confirmation |
| Peter corrects your approach | "Got it" | Write it to a durable artifact |
| Ready to implement | Start coding | Describe the plan, wait for confirmation |
| Another YAC says fix is deployed | Update your code | Curl the endpoint first |

---

*This manifest encodes lessons from the WIP Constellation Experiment. Each rule exists because an agent violated it — some multiple times. The rules will evolve as new patterns emerge. When Peter adds a correction that applies broadly, update this document.*
