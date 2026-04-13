# YAC Coding Manifest

Rules for every coding YAC. Non-negotiable. Each rule exists because an agent violated it — some multiple times.

---

## 1. Bug Report Before Workaround

If you discover a bug or gap in a library, API, tool, or type definition:

1. **File a CASE first.** Describe the bug, expected behavior, and how you found it.
2. **Only then** implement a temporary workaround if you're blocked.
3. **Never** silently work around a gap and call it "not worth a case."

Every gap filed is a gap fixed for the next agent. Every gap silently worked around is a gap the next agent hits again.

**What counts as a gap:**
- Missing type field (TypeScript or Python)
- Missing API endpoint
- Wrong response shape
- Library bug (e.g., option spread order overwriting callbacks)
- Missing hook, utility, or CLI tool

The cost of a redundant case is one response. The cost of an unfiled gap is every future agent hitting the same wall.

## 2. Think Before You Code

Before implementing non-trivial changes:

1. **Describe your plan** in plain language. What will change, why, and what could go wrong.
2. **Wait for confirmation** on anything that touches safety, security, or shared interfaces.
3. **Do not** start coding while discussing design. Peter will tell you when to implement.

If Peter says "DO NOT CODE yet" — stop. Think. Answer his questions. The plan always improves.

## 3. Verify Before You Assume

- **Always verify the actual interface** before writing code against it.
  - APP-YAC: curl the endpoint. Check the response shape. Don't trust docs or memory.
  - BE-YAC: read the client TypeScript types before changing API response models. Your change may break downstream consumers silently.
- **Always check your library/package versions** before filing a case about missing features. They may already exist in a newer version.
- **Always check case history** before filing. Your question may already be answered.

The previous YAC guessed. You won't.

## 4. Type Safety Is Non-Negotiable

Client types and API models must stay in sync. When they drift, bugs hide.

**APP-YAC (TypeScript):**
- **Never** use `as any` to bypass type restrictions. `as any` defeats the entire client-side type safety layer.
- If types don't match the API, that's a bug — file a CASE (Rule 1).
- If you must cast temporarily while waiting for a fix, use a narrow intersection type (`as ItemType & { field: string }`), never `as any`.

**BE-YAC (Python):**
- **Never** use `# type: ignore` to suppress Pydantic validation issues.
- When adding fields to a Pydantic response model, check whether the corresponding TypeScript type in `@wip/client` needs updating. If it does, update it or file a CASE.
- **Always rebuild the container** after committing API changes. Code committed but not deployed is code that doesn't exist. Stale containers cause downstream agents to file false bug reports.

**Both:**
- Request types must match the API. If the API accepts a field, the client type must include it.
- Response types must match the API. If the API returns a field, the client type must expose it. Missing response fields cause silent data loss.
- The server has `StrictModel(extra='forbid')`. The client should be equally strict.

## 5. Don't Bypass Safety Mechanisms

If a feature exists for safety (deletion_mode, confirmation gates, namespace scoping):

- **Never silently override it.** Setting `deletion_mode='full'` before delete without user knowledge is bypassing a safety mechanism.
- **Make safety visible.** Show the user what protection exists and let them explicitly choose to proceed.
- **Two-step confirmation** for destructive operations on protected resources.

## 6. Stay In Scope

Fix what you're asked to fix. If you discover adjacent issues while working:

1. **File them as cases.** Don't fix them in the same session unless Peter approves the scope expansion.
2. **Don't audit-and-fix unsolicited.** "Fix this one type" does not mean "audit all 52 drifts." That may be the right call — but it's Peter's call, not yours.
3. **Report what you found** and let Peter decide the priority.

An unattended agent that expands scope burns context windows on unrequested work.

## 7. Cross-Agent Communication

When another YAC's work affects yours:

- **Read the case response fully** before acting on it. Don't skim.
- **Verify that fixes are actually deployed**, not just committed. Containers may be stale. Curl the endpoint. Check the version.
- **Update the case** with your findings — success or failure. The case is the institutional memory.

Trust but verify.

## 8. Encode What You Learn

When Peter corrects your approach:

1. Ask yourself: "Would the next YAC make the same mistake?"
2. If yes, write it down: `/lesson`, dead ends section, or CLAUDE.md update suggestion.
3. "Got it, won't happen again" is meaningless — **you** won't exist next session. The lesson must be in a file.

## 9. Speed Is Secondary

> "Be thorough, and think deep, do not cut corners. Speed is secondary, this has to be rock solid and powerful."

- A correct implementation that took an hour beats a fast one that needs three rounds of fixes.
- A well-filed bug report is more valuable than a working workaround.
- "We don't need a working app. We need bug reports."

---

## Quick Reference

| Situation | Wrong | Right |
|-----------|-------|-------|
| Library/API has a gap | Work around it silently | File CASE first, then workaround if blocked |
| Type doesn't match API | `as any` / `# type: ignore` | File CASE, use narrow cast if blocked |
| API returns unexpected shape | Adjust your code to match | Verify with curl/read types, file CASE |
| Feature seems missing from library | Implement it yourself | Check latest version, check case history |
| Safety mechanism blocks you | Override it silently | Make it visible, add confirmation |
| Found adjacent bugs while working | Fix them all now | File cases, let Peter prioritize |
| Peter corrects your approach | "Got it" | Write it to a durable artifact |
| Ready to implement | Start coding | Describe the plan, wait for confirmation |
| Another YAC says fix is deployed | Update your code | Curl the endpoint first |
| Changed an API response model | Commit and move on | Check client types, rebuild container |
