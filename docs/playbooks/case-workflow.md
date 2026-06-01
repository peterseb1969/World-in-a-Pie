# Case Workflow Playbook

Full handler reference for the `/case` slash command. The slash command stub at `.claude/commands/case.md` performs the directory pre-flight, then reads this file and dispatches based on `$ARGUMENTS`.

By the time you are reading this, `yac-discussions/` is known to exist. Do not re-check.

## Subcommands

- `/case file [optional Peter comment]` — file a new case about a bug, question, or platform gap
- `/case list` — list all open/responded cases (one-line summary each)
- `/case read <number>` — read a specific case in full, including all comments and responses
- `/case respond <number>` — append a response to an existing case
- `/case comment <number> [text]` — add a comment (anyone: filer, responder, or Peter via a YAC)
- `/case close <number>` — close without implementation (won't-fix, not-an-issue, deferred, handled manually)
- `/case implement <number>` — apply the proposed patch, then close as implemented

## Prerequisites

You must have a session ID (see YAC Reporting section in CLAUDE.md).

## Filename Convention

Case files are named: `CASE-<NN>-<status>-<slug>.md`

- `<NN>` — a short number, unique within the directory. **Server-assigned** at allocation (the `CASE-<n>` Registry synonym of the case's UUID; `case_helper.sh claim` retired — see `/case file`). The flat filename carries it for the FS record; identity lives in kb.
- `<status>` — one of: `open`, `responded`, `closed`, `implemented`
- `<slug>` — 2-4 word kebab-case topic

Examples:
```
CASE-01-open-unknown-fields.md
CASE-02-responded-doc-arch.md
CASE-03-closed-relative-baseurl.md
CASE-04-implemented-doc-faq.md
```

**Status changes require renaming the file.** When updating status in the frontmatter, also rename the file to match.

## Finding Cases by Number

When a command takes `<number>`, match it against the `CASE-<NN>-` prefix in the filename. For example, `/case read 3` finds the file starting with `CASE-03-`. The number is stable — it never changes, even when the file is renamed for status updates.

```bash
ls yac-discussions/CASE-03-*.md 2>/dev/null
```

---

## Handling `/case file`

> **Filing discipline (do not skip).** Filing a NEW case ALWAYS goes through the **served allocator** `case_allocate.py` (step 3 below) — never the `Write` tool, never hand-picking a number. The number is server-assigned: `case_allocate` reads the current max, then **claims the `CASE-<n>` Registry synonym atomically** and creates the CASE_RECORD; a concurrent filer who grabbed `CASE-<n>` first causes a `synonym_conflict`, and the allocator advances to `<n>+1` and retries. **The atomic synonym claim is the serializer** (CASE-427/436) — no FS lock, no client-side number reasoning, distinct numbers by construction. If you find yourself reasoning about case numbers ("what's the next available?", "is N taken?"), stop — `case_allocate` does it. This replaces the old FS `case-helper.sh claim` (CASE-67/CASE-301 collisions, CASE-306 discipline) with server-side allocation per CASE-425/437; the FS claim is retired.

### 1. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

### 2. Create a slug

Infer a short slug from context: `unknown-fields`, `relative-baseurl`, `template-update-missing`. 2-4 words, lowercase kebab-case (matches the regex `^[a-z0-9]+(-[a-z0-9]+)*$`).

### 3. Allocate a case number (server-side, atomic)

```bash
bash ../FR-YAC/tools/kb-client.sh case_allocate.py \
  --title "<short case title>" --filed-by "<your session ID>" \
  --type <bug|question|request|platform-gap> --severity <blocks-me|annoying|fyi> \
  --component <document-store|registry|scaffold|mcp-server|wip-client|wip-react|wip-proxy|wip-auth|reporting-sync|other>
# Prints: CASE-<n> <document_id>
```

`kb-client.sh` fetches the version-matched KB client from the running instance (the loaders are APP-KB-owned and served — CASE-437) and runs the allocator. `case_allocate` reserves `<n>` by **atomically claiming the `CASE-<n>` synonym** and creating the CASE_RECORD (`data.case_number=<n>`, `data.status=open`). On `synonym_conflict` it advances `<n>+1` and retries — concurrent filers get distinct numbers by construction. Note the printed `<n>`; it is the assigned case number.

(Path to `kb-client.sh` is from your YAC's repo root; the wrapper itself is the one client-side bootstrap — it fetches the served client. Adjust the path if your repo layout differs.)

### 4. Write the case file (the FS record)

`case_allocate` has created the kb record + reserved the number. Now write the flat file `yac-discussions/CASE-<n>-open-<slug>.md` (the git-tracked FS record), filling the assigned `<n>` into the frontmatter `case:` field:

```markdown
---
case: <NN>
filed_by: <your session ID>
app: <your app name, or "backend">
type: <bug | question | request | platform-gap>
severity: <blocks-me | annoying | fyi>
component: <wip-client | document-store | registry | scaffold | mcp-server | wip-react | wip-proxy | wip-auth | reporting-sync | other>
status: open
filed: <YYYY-MM-DD HH:MM>
---

## Problem

<What happened, with evidence — error messages, unexpected behavior, missing functionality.
Be specific enough that a BE-YAC with no knowledge of your app can understand.>

## Expected

<What should have happened.>

## Workaround

<What you're doing in the meantime. "None" if blocked. This matters — workarounds hide problems.>

## Peter's Take

<If Peter provided a comment with `/case file`, put it here verbatim. If no comment, omit this section entirely.>
```

### 5. Sync the body to wip-kb (resolve-then-update)

`case_allocate` already created the record (step 3). Now push the full file body into it — `add-to-kb` **resolves the `CASE-<n>` synonym to the document_id and updates in place** (v2 resolve-then-update; it does NOT create — the doc already exists):

```bash
bash ../FR-YAC/tools/kb-client.sh add-to-kb.py yac-discussions/CASE-<n>-open-<slug>.md
```

The served client:
- Resolves `CASE-<n>` → document_id and updates `data.body` with the full file content (JSON Merge Patch, a new version; no duplicate — v2 CASE_RECORD has `identity_fields:[]`, so a create would append — resolve-then-update is mandatory).
- Derives `REFERENCES` edges from your frontmatter `related:` field (each `CASE-N` mention → an outbound edge to the matching CASE_RECORD via its synonym; targets not yet in KB are silently skipped).
- Handshakes against the instance manifest and refuses to write on `schema_version` skew (no-skew guarantee).
- Writes one canonical instance (the one that served the client) — no local+remote dual-write.

This step is **not optional** — without it the record carries only the title/metadata from allocation, not the body. CASE-307 names the dual-write; CASE-425/437 the v2 resolve-then-update.

### 6. Confirm

Tell Peter the case number, slug, file path, and the kb document_id printed by the script.

---

## Handling `/case list`

### 1. Scan for cases

```bash
ls yac-discussions/CASE-*.md 2>/dev/null
```

If no files, say "No cases filed" and stop.

### 2. Read frontmatter of each case

For each file, read just the YAML frontmatter (case number, status, type, severity, filed_by, component).

### 3. Present a summary

Show non-closed cases first, then recently closed/implemented (last 7 days). One line each:

```markdown
## Open Cases

| # | Status | Severity | Type | Component | Filed by | Slug |
|---|--------|----------|------|-----------|----------|------|
| 01 | open | blocks-me | bug | document-store | APP-AA-20260401-2139 | unknown-fields |
| 02 | responded | annoying | request | mcp-server | APP-AA-20260401-2139 | no-update-template |

## Recently Closed/Implemented

| # | Status | Type | Slug |
|---|--------|------|------|
| 03 | implemented | bug | relative-baseurl |
| 04 | closed | request | wont-fix-example |
```

Filter by relevance:
- **BE-YACs:** show all cases (you maintain the platform)
- **APP-YACs:** show cases filed by your app prefix, or cases with `status: responded` where you are the filer

---

## Handling `/case read <number>`

### 1. Find the case

```bash
ls yac-discussions/CASE-<NN>-*.md 2>/dev/null
```

Read the matching file. If it doesn't exist, tell Peter and stop.

### 2. Present the full case

Show the complete file — frontmatter, problem, expected, workaround, Peter's take (if any), and all comments/responses/resolution in order.

---

## Handling `/case respond <number>`

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

### 2. Analyse before responding

**Do not jump to implementation.** Before writing a response:

1. **Understand the root cause.** Read the relevant source code. Don't guess from the symptom description — verify where the bug actually lives.
2. **Check the proposed solution (if any).** Cases often include a "Suggested Fix" or "Workaround" from the filer. **Do not assume the proposed solution is correct.** The filer sees their side; you see the platform. Ask:
   - Does this actually solve the root cause, or just the symptom?
   - Does the library/framework validate assumptions this solution breaks? (e.g., OIDC issuer validation, identity hash scoping, bulk-first response contracts)
   - Are there edge cases the filer couldn't see from their vantage point?
   - Is there a simpler or more principled solution?
3. **If you find a better solution**, describe both in your response: what was proposed, why it doesn't fully work, and what you recommend instead. Update the case — don't silently implement a different fix.
4. **If the proposed solution IS correct**, say so and explain why. Show your analysis, not just "looks right, implementing."

The goal: every response demonstrates that the solution was analysed, not just executed. CASE-50 was implemented blindly (OIDC issuer split) and broke because the library validates issuer consistency. CASE-36 was analysed properly (three agents contributed different perspectives) and produced the right fix. Be like CASE-36.

### 3. Append a response section

Append to the case file:

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

### Analysis
<What you checked, what the root cause is, whether the proposed solution works and why/why not.>

### Fix
<Your proposed or implemented fix. Reference specific files, lines, commits.>
```

### 3. Update the status and rename

Change `status: open` to `status: responded` in the frontmatter.

Rename the file: `CASE-<NN>-open-<slug>.md` → `CASE-<NN>-responded-<slug>.md`

```bash
mv yac-discussions/CASE-<NN>-open-<slug>.md yac-discussions/CASE-<NN>-responded-<slug>.md
```

### 4. Mirror to wip-kb

Re-run the loader to refresh the kb record body (idempotent — updates the existing CASE_RECORD via JSON Merge Patch, no duplicates):

```bash
bash ../FR-YAC/tools/kb-client.sh add-to-kb.py yac-discussions/CASE-<NN>-responded-<slug>.md
```

This step is **not optional** — without it, the kb body drifts from the flat-file source. CASE-307 names the design.

### 5. Confirm

Tell Peter what you responded and the case number.

---

## Handling `/case comment <number>`

Add a follow-up comment to an existing case. Use this for clarifications, additional context, Peter's input, or questions between filer and responder.

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

### 2. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

### 3. Append a comment section

Append to the case file:

```markdown
## Comment — <your session ID> (<YYYY-MM-DD HH:MM>)

<The comment. If Peter dictated this, attribute it: "Peter: <his words>">
```

If the user provided text with the command (e.g., `/case comment 3 This is urgent`), use that as the comment body. Otherwise, infer from the current conversation context.

### 4. Mirror to wip-kb

Re-run the loader to refresh the kb record body (idempotent — comments don't change status, but the appended body must propagate to kb):

```bash
bash ../FR-YAC/tools/kb-client.sh add-to-kb.py yac-discussions/CASE-<NN>-*.md
```

This step is **not optional** — without it, the kb body drifts from the flat-file source. CASE-307 names the design.

### 5. Confirm

Tell Peter the comment was added.

---

## Handling `/case close <number>`

Close a case without implementing anything. Use for: won't-fix, not-an-issue, deferred, or Peter handled it manually.

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

### 2. Append a resolution

```markdown
## Resolution — <your session ID> (<YYYY-MM-DD HH:MM>)

<Won't fix / Not an issue / Deferred / Handled manually — brief explanation.>
```

### 3. Update the status and rename

Change `status:` to `status: closed` in the frontmatter.

Rename: `CASE-<NN>-<old-status>-<slug>.md` → `CASE-<NN>-closed-<slug>.md`

### 4. Mirror to wip-kb

Re-run the loader to refresh the kb record body and status:

```bash
bash ../FR-YAC/tools/kb-client.sh add-to-kb.py yac-discussions/CASE-<NN>-closed-<slug>.md
```

This step is **not optional** — without it, the kb body drifts from the flat-file source. CASE-307 names the design.

### 5. Confirm

Tell Peter the case is closed and why.

---

## Handling `/case implement <number>`

Apply the proposed patch from a responded case, then close it as implemented. This is the "do the work" command.

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. Read the full case, including all responses.

If the case has no `## Response` section with a proposed patch, tell Peter: "Case <NN> has no proposed patch to implement. Use `/case respond` first." Then stop.

### 2. Verify the proposed fix before applying

Find the most recent `## Response` section. Read the analysis and proposed fix.

**Before touching any code:**
- Does the analysis in the response convince you? If not, do your own analysis and update the case first.
- Read the target files. Has the code changed since the response was written? The fix may no longer apply or may be obsolete.
- Check whether the fix has side effects the responder didn't consider (other callers, other services, test suites).
- If anything is unclear or wrong, update the case with your findings — don't implement a fix you don't trust.

### 3. Apply each change

For each proposed change:
- Find the target file (referenced in the case or response)
- Locate the "Current text" quoted in the patch
- Replace with the "Proposed text"
- If the current text doesn't match (file has changed since the review), flag it and skip that change — don't force it

### 4. Show what changed

```bash
git diff
```

Tell Peter what was applied and what was skipped (if any). Let Peter review the diff before committing.

### 5. Update the status and rename

After Peter confirms (or immediately if the changes are clean):

Change `status:` to `status: implemented` in the frontmatter.

Append:

```markdown
## Implementation — <your session ID> (<YYYY-MM-DD HH:MM>)

Applied N of M proposed changes. <Note any skipped changes and why.>
```

Rename: `CASE-<NN>-<old-status>-<slug>.md` → `CASE-<NN>-implemented-<slug>.md`

### 6. Mirror to wip-kb

Re-run the loader to refresh the kb record body and status:

```bash
bash ../FR-YAC/tools/kb-client.sh add-to-kb.py yac-discussions/CASE-<NN>-implemented-<slug>.md
```

This step is **not optional** — without it, the kb body drifts from the flat-file source. CASE-307 names the design.

### 7. Confirm

Tell Peter the case is implemented. Do NOT commit — Peter decides when to commit.

---

## When to use `/case file`

- You hit a bug in a platform component (document-store, registry, MCP server, client libs)
- You need a feature that doesn't exist (missing MCP tool, missing React hook)
- You discover a platform gap (behavior that contradicts docs or conventions)
- Peter tells you to file a case

## When NOT to use `/case file`

- Routine bugs in your own app code (fix them yourself)
- Questions you can answer by reading docs or MCP resources
- Peter said "off the record"
