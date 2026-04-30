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

- `<NN>` — a short number (zero-padded to 2 digits), unique within the directory. Assigned at filing time as the next available number.
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

### 1. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

### 2. Create a slug

Infer a short slug from context: `unknown-fields`, `relative-baseurl`, `template-update-missing`. 2-4 words, lowercase kebab-case (matches the regex `^[a-z0-9]+(-[a-z0-9]+)*$`).

### 3. Claim a case number AND filename atomically

```bash
bash yac-discussions/case-helper.sh claim <slug>
# Echoes: yac-discussions/CASE-<NN>-open-<slug>.md
```

The script creates the file with a placeholder body (`_(case body in progress — DO NOT FILE NEW CASE WITH THIS NUMBER)_`). **The file existence is the lock.** Two YACs running this concurrently get different numbers — the first to win the file-create takes `<NN>`; the second hits a noclobber error, retries with `<NN>+1`, and gets that.

This replaces the older "find highest, add 1, then write 2–4 minutes later" flow that produced the CASE-67 collision on Apr 27 (race window: a YAC chews on the case body for minutes between getting `next` and writing the file).

If your slug is invalid (uppercase, spaces, special chars), the script errors with a hint. Pick a different slug and retry.

### 4. Fill in the case body

The file now exists at the path the script echoed, with the placeholder line as its only content. Open it and replace the placeholder with proper frontmatter + body:

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

### 5. Confirm

Tell Peter the case number, slug, and file path.

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

### 4. Confirm

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

### 4. Confirm

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

### 4. Confirm

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

### 6. Confirm

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
