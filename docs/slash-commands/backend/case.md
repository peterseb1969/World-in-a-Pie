Cross-agent case management. File bugs, questions, or requests for other YACs. Read and respond to cases filed by others.

### Usage

- `/case file [optional Peter comment]` — file a new case about a bug, question, or platform gap
- `/case list` — list all open/responded cases (one-line summary each)
- `/case read <number>` — read a specific case in full, including all comments and responses
- `/case respond <number>` — append a response to an existing case
- `/case comment <number> [text]` — add a comment (anyone: filer, responder, or Peter via a YAC)
- `/case close <number>` — close without implementation (won't-fix, not-an-issue, deferred, handled manually)
- `/case implement <number>` — apply the proposed patch, then close as implemented

### Prerequisites

You must have a session ID (see YAC Reporting section in CLAUDE.md).

### Shared Directory

Cases live in `yac-discussions/` relative to your project root. This directory is a symlink to the shared case store. If Peter has enabled cross-agent cases for your project, the symlink exists. If not, it doesn't.

**Before any `/case` operation, check that the directory exists:**

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If `yac-discussions/` does not exist, tell Peter: "Cross-agent cases are not enabled for this project. To enable, symlink `yac-discussions/` to the shared case store." Then stop.

### Filename Convention

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

### Finding Cases by Number

When a command takes `<number>`, match it against the `CASE-<NN>-` prefix in the filename. For example, `/case read 3` finds the file starting with `CASE-03-`. The number is stable — it never changes, even when the file is renamed for status updates.

```bash
ls yac-discussions/CASE-03-*.md 2>/dev/null
```

---

### Handling `/case file`

#### 1. Check directory exists

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If missing, report and stop (see above).

#### 2. Get the current time

```bash
date '+%Y%m%d-%H%M'
```

#### 3. Assign a case number

Find the highest existing case number and add 1:

```bash
ls yac-discussions/CASE-*.md 2>/dev/null | sed 's/.*CASE-\([0-9]*\)-.*/\1/' | sort -n | tail -1
```

If no cases exist, start at 01. Zero-pad to 2 digits (01–99). If you somehow reach 100+, use 3 digits.

#### 4. Create a slug

Infer a short slug from context: `unknown-fields`, `relative-baseurl`, `template-update-missing`. 2-4 words, kebab-case.

#### 5. Write the case file

Create `yac-discussions/CASE-<NN>-open-<slug>.md`:

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

#### 6. Confirm

Tell Peter the case number, slug, and file path.

---

### Handling `/case list`

#### 1. Check directory exists

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If missing, report and stop.

#### 2. Scan for cases

```bash
ls yac-discussions/CASE-*.md 2>/dev/null
```

If no files, say "No cases filed" and stop.

#### 3. Read frontmatter of each case

For each file, read just the YAML frontmatter (case number, status, type, severity, filed_by, component).

#### 4. Present a summary

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

### Handling `/case read <number>`

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find the case

```bash
ls yac-discussions/CASE-<NN>-*.md 2>/dev/null
```

Read the matching file. If it doesn't exist, tell Peter and stop.

#### 3. Present the full case

Show the complete file — frontmatter, problem, expected, workaround, Peter's take (if any), and all comments/responses/resolution in order.

---

### Handling `/case respond <number>`

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

#### 3. Append a response section

Append to the case file:

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

<Your diagnosis, fix, answer, or what you need to know.
Reference specific commits if you fixed something.>
```

#### 4. Update the status and rename

Change `status: open` to `status: responded` in the frontmatter.

Rename the file: `CASE-<NN>-open-<slug>.md` → `CASE-<NN>-responded-<slug>.md`

```bash
mv yac-discussions/CASE-<NN>-open-<slug>.md yac-discussions/CASE-<NN>-responded-<slug>.md
```

#### 5. Confirm

Tell Peter what you responded and the case number.

---

### Handling `/case comment <number>`

Add a follow-up comment to an existing case. Use this for clarifications, additional context, Peter's input, or questions between filer and responder.

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

#### 3. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

#### 4. Append a comment section

Append to the case file:

```markdown
## Comment — <your session ID> (<YYYY-MM-DD HH:MM>)

<The comment. If Peter dictated this, attribute it: "Peter: <his words>">
```

If the user provided text with the command (e.g., `/case comment 3 This is urgent`), use that as the comment body. Otherwise, infer from the current conversation context.

#### 5. Confirm

Tell Peter the comment was added.

---

### Handling `/case close <number>`

Close a case without implementing anything. Use for: won't-fix, not-an-issue, deferred, or Peter handled it manually.

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

#### 3. Append a resolution

```markdown
## Resolution — <your session ID> (<YYYY-MM-DD HH:MM>)

<Won't fix / Not an issue / Deferred / Handled manually — brief explanation.>
```

#### 4. Update the status and rename

Change `status:` to `status: closed` in the frontmatter.

Rename: `CASE-<NN>-<old-status>-<slug>.md` → `CASE-<NN>-closed-<slug>.md`

#### 5. Confirm

Tell Peter the case is closed and why.

---

### Handling `/case implement <number>`

Apply the proposed patch from a responded case, then close it as implemented. This is the "do the work" command.

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. Read the full case, including all responses.

If the case has no `## Response` section with a proposed patch, tell Peter: "Case <NN> has no proposed patch to implement. Use `/case respond` first." Then stop.

#### 3. Extract the proposed changes

Find the most recent `## Response` section. Look for the `### Proposed Changes` or `### Proposed Patch` subsection. These contain the concrete text changes to apply.

#### 4. Apply each change

For each proposed change:
- Find the target file (referenced in the case or response)
- Locate the "Current text" quoted in the patch
- Replace with the "Proposed text"
- If the current text doesn't match (file has changed since the review), flag it and skip that change — don't force it

#### 5. Show what changed

```bash
git diff
```

Tell Peter what was applied and what was skipped (if any). Let Peter review the diff before committing.

#### 6. Update the status and rename

After Peter confirms (or immediately if the changes are clean):

Change `status:` to `status: implemented` in the frontmatter.

Append:

```markdown
## Implementation — <your session ID> (<YYYY-MM-DD HH:MM>)

Applied N of M proposed changes. <Note any skipped changes and why.>
```

Rename: `CASE-<NN>-<old-status>-<slug>.md` → `CASE-<NN>-implemented-<slug>.md`

#### 7. Confirm

Tell Peter the case is implemented. Do NOT commit — Peter decides when to commit.

---

### When to use `/case file`

- You hit a bug in a platform component (document-store, registry, MCP server, client libs)
- You need a feature that doesn't exist (missing MCP tool, missing React hook)
- You discover a platform gap (behavior that contradicts docs or conventions)
- Peter tells you to file a case

### When NOT to use `/case file`

- Routine bugs in your own app code (fix them yourself)
- Questions you can answer by reading docs or MCP resources
- Peter said "off the record"
