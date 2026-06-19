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

## The served KB client (one-time setup, self-refreshing)

All kb reads/writes in this playbook go through the **served KB client** — fetched
from the running KB instance itself (CASE-437/440), version-matched, with **zero
FR-YAC dependency**. The only inputs are the instance URL and your API key:

```bash
curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
  "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh
```

This materializes the bundle (loaders + the `kb-client.sh` runner + this playbook)
into `~/.cache/wip-kb-client/`. Run it once per machine; every subsequent
invocation of the runner **self-refreshes when the instance's bundle digest
changes**, so you never re-install by hand:

```bash
bash ~/.cache/wip-kb-client/kb-client.sh <script.py> [args...]
```

If `~/.cache/wip-kb-client/kb-client.sh` is missing, run the install one-liner —
that is the whole recovery procedure.

**Case WRITES go through the gateway verbs (CASE-464), not the runner.** Set
once per shell:

```bash
BASE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb"
KEY="$(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")"   # or your YAC's runtime key
```

The verbs are server-side domain logic: allocation, the `CASE-<n>` synonym
claim, REFERENCES edges, the status machine, and race-safe section appends
(`if_match`) all live in the gateway. Reads stay on the runner's
`case-fetch.py` for now (the read API `GET $BASE/cases…` also works;
`?since=` under-reports on the cluster until CASE-466 closes).

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

**Status lives in kb (`data.status`), maintained by the gateway verbs.** Flat files are optional write-staging (CASE-464) — regenerable via `GET $BASE/cases/<n>`. If you keep one, renaming it to match the status is a courtesy for FS browsing, not a required step; there is no rename-and-re-mirror flow anymore.

## Finding Cases by Number

When a command takes `<number>`, match it against the `CASE-<NN>-` prefix in the filename. For example, `/case read 3` finds the file starting with `CASE-03-`. The number is stable — it never changes, even when the file is renamed for status updates.

```bash
ls yac-discussions/CASE-03-*.md 2>/dev/null
```

---

## Handling `/case file`

> **Filing discipline (do not skip).** Filing a NEW case is ONE gateway call — `POST $BASE/cases` (CASE-464) — never the `Write` tool with a hand-picked number, never client-side number reasoning. The server allocates the number, claims the `CASE-<n>` Registry synonym atomically (CASE-427/436 — the serializer; concurrent filers get distinct numbers by construction), creates the record with `data.status=open`, and derives REFERENCES edges from `related`. If you find yourself asking "what's the next available number?", stop — the verb does it. (History: FS `case-helper.sh claim` retired by CASE-440; the served `case_allocate.py` retired by CASE-464.)

### 1. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

### 2. Create a slug

Infer a short slug from context: `unknown-fields`, `relative-baseurl`, `template-update-missing`. 2-4 words, lowercase kebab-case (matches the regex `^[a-z0-9]+(-[a-z0-9]+)*$`).

### 3. Compose the case body

Full case markdown, frontmatter included. Leave `case:` as `<NN>` — the server assigns the number; fill it into your staged copy afterwards if you keep one:

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

Optionally stage it to `yac-discussions/CASE-<n>-open-<slug>.md` after the POST returns the number — the staged file is a local convenience, regenerable via `GET $BASE/cases/<n>`; kb is the record.

### 4. File it (one gateway call)

```bash
curl -fsSk -X POST "$BASE/cases" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title":"<short case title>","filed_by":"<your session ID>",
       "type":"<bug|question|request|platform-gap>","severity":"<blocks-me|annoying|fyi>",
       "component":"<component>","app":"<your app>",
       "body":"<the full case markdown from step 3>","related":["CASE-12","CASE-340"]}'
# -> 201 {"case":N,"synonym":"CASE-N","document_id":"…","edges":K}
```

Allocation, synonym, `data.status=open`, and REFERENCES edges are all server-side. A 422 names the invalid field; retries are safe (a failed create allocates nothing).

### 5. Confirm

Tell Peter the case number, the kb document_id, and the staged file path if you kept one.

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

### 3. Post the response (one gateway call)

Compose the section content — `### Analysis` / `### Fix` subsections as before — **without** the `## Response — …` heading (the server composes it from `author` + its own timestamp), then:

```bash
curl -fsSk -X POST "$BASE/cases/<n>/respond" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"author":"<your session ID>","text":"<the section markdown WITHOUT the ## heading>"}'
# -> 200 {"case":N,"status":"responded","doc_version":V} | 422 illegal transition | 404 unknown | 409 after 3 conflict retries
```

The append is race-safe (`if_match` + re-read retry — concurrent writers both land, CASE-462's lesson) and the status transition (open→responded) is enforced server-side. No file rename, no mirror step — kb is the record; refresh any staged flat file from `GET $BASE/cases/<n>` if you keep one.

### 4. Confirm

Tell Peter the case number and the returned status.

---

## Handling `/case comment <number>`

Add a follow-up comment to an existing case. Use this for clarifications, additional context, Peter's input, or questions between filer and responder.

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

### 2. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

### 3. Post the comment (one gateway call)

```bash
curl -fsSk -X POST "$BASE/cases/<n>/comment" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"author":"<your session ID>","text":"<the comment markdown WITHOUT the ## heading>"}'
# -> 200 (no status change — comments are legal in every state, including terminal)
```

Race-safe append; the server composes the `## Comment — <author> (<ts>)` heading. No rename, no mirror.

### 4. Confirm

One line to Peter: comment posted on CASE-<n>.

---

## Handling `/case close <number>`

Close a case without implementing anything. Use for: won't-fix, not-an-issue, deferred, or Peter handled it manually.

### 1. Find and read the case

Find `yac-discussions/CASE-<NN>-*.md`. If it doesn't exist, tell Peter and stop.

### 2. Post the resolution (one gateway call)

Compose the resolution rationale (won't-fix / not-an-issue / deferred / handled manually — with the why), then:

```bash
curl -fsSk -X POST "$BASE/cases/<n>/close" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"author":"<your session ID>","text":"<resolution markdown WITHOUT the ## heading>"}'
# -> 200 {"case":N,"status":"closed",…} | 422 if the transition is illegal
```

Terminal: after this, comments remain legal but no further transitions. No rename, no mirror.

### 3. Confirm

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

### 5. Post the implementation record (one gateway call)

Compose the implementation section — what was applied, where, verification results — then:

```bash
curl -fsSk -X POST "$BASE/cases/<n>/implement" -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"author":"<your session ID>","text":"<implementation markdown WITHOUT the ## heading>"}'
# -> 200 {"case":N,"status":"implemented",…}
```

Terminal; server-side transition; no rename, no mirror.

### 6. Confirm

Tell Peter what was applied and that the case is implemented.
