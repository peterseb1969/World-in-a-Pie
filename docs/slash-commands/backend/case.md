Cross-agent case management. File bugs, questions, or requests for other YACs. Read and respond to cases filed by others.

### Usage

- `/case file [optional Peter comment]` — file a new case about a bug, question, or platform gap
- `/case list` — list all open/responded cases (one-line summary each)
- `/case read <case-id>` — read a specific case in full, including all comments and responses
- `/case respond <case-id>` — append a response to an existing case
- `/case comment <case-id> [text]` — add a comment (anyone: filer, responder, or Peter via a YAC)
- `/case close <case-id>` — mark a case as resolved

### Prerequisites

You must have a session ID (see YAC Reporting section in CLAUDE.md).

### Shared Directory

Cases live in `yac-discussions/` relative to your project root. This directory is a symlink to the shared case store. If Peter has enabled cross-agent cases for your project, the symlink exists. If not, it doesn't.

**Before any `/case` operation, check that the directory exists:**

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If `yac-discussions/` does not exist, tell Peter: "Cross-agent cases are not enabled for this project. To enable, symlink `yac-discussions/` to the shared case store." Then stop.

Each case is a single markdown file named `CASE-YYYYMMDD-HHMM-<slug>.md`.

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

#### 3. Create a slug

Infer a short slug from context: `unknown-fields`, `relative-baseurl`, `template-update-missing`. 2-4 words, kebab-case.

#### 4. Write the case file

Create `yac-discussions/CASE-YYYYMMDD-HHMM-<slug>.md`:

```markdown
---
case: CASE-YYYYMMDD-HHMM-<slug>
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

#### 5. Confirm

Tell Peter the case was filed, its ID, and the file path.

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

For each file, read just the YAML frontmatter (case ID, status, type, severity, filed_by, component).

#### 4. Present a summary

Show non-closed cases first, then recently closed (last 7 days). One line each:

```markdown
## Open Cases

- **CASE-20260401-2205-unknown-fields** (bug, blocks-me, document-store) — filed by APP-AA-20260401-2139. [open]
- **CASE-20260401-2210-no-update-template** (request, annoying, mcp-server) — filed by APP-AA-20260401-2139. [responded]

## Recently Closed

- **CASE-20260401-2150-relative-baseurl** (bug, blocks-me, wip-client) — filed by APP-AA-20260401-1754. [closed]
```

Filter by relevance:
- **BE-YACs:** show all cases (you maintain the platform)
- **APP-YACs:** show cases filed by your app prefix, or cases with `status: responded` where you are the filer

---

### Handling `/case read <case-id>`

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find the case

Read `yac-discussions/<case-id>.md`. If it doesn't exist, tell Peter and stop.

#### 3. Present the full case

Show the complete file — frontmatter, problem, expected, workaround, Peter's take (if any), and all comments/responses/resolution in order.

---

### Handling `/case respond <case-id>`

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Read `yac-discussions/<case-id>.md`. If it doesn't exist, tell Peter and stop.

#### 3. Append a response section

Append to the case file:

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

<Your diagnosis, fix, answer, or what you need to know.
Reference specific commits if you fixed something.>
```

#### 4. Update the status

Change `status: open` to `status: responded` in the frontmatter.

#### 5. Confirm

Tell Peter what you responded and the case ID.

---

### Handling `/case comment <case-id>`

Add a follow-up comment to an existing case. Use this for clarifications, additional context, Peter's input, or questions between filer and responder.

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Read `yac-discussions/<case-id>.md`. If it doesn't exist, tell Peter and stop.

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

If the user provided text with the command (e.g., `/case comment CASE-xxx This is urgent`), use that as the comment body. Otherwise, infer from the current conversation context.

#### 5. Confirm

Tell Peter the comment was added.

---

### Handling `/case close <case-id>`

#### 1. Check directory exists

If `yac-discussions/` missing, report and stop.

#### 2. Find and read the case

Read `yac-discussions/<case-id>.md`. Verify it has a response or that you're closing it for another reason.

#### 3. Append a resolution

```markdown
## Resolution — <your session ID> (<YYYY-MM-DD HH:MM>)

<Confirmed fixed / Not an issue / Won't fix — brief explanation.>
```

#### 4. Update the status

Change `status:` to `status: closed` in the frontmatter.

#### 5. Confirm

Tell Peter the case is closed.

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
