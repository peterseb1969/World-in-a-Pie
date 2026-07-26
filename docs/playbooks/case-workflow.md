# Case Workflow Playbook

Handler reference for the `/wip-case` slash command. The command reads this file
and dispatches on `$ARGUMENTS`.

**Cases live in the KB, not on disk.** There are no `yac-discussions/CASE-*.md`
files to scan, rename, or stage — the KB is the record. Every read and write goes
through the **served KB client** (`case-fetch.py` to read, `kb-write.py` to write),
which talks only to the KB **gateway** (never the document-store backend).

## Subcommands

- `/wip-case file [Peter comment]` — file a new case (bug, question, request, gap)
- `/wip-case list` — list open/responded cases
- `/wip-case read <n>` — read a case in full (body + all responses)
- `/wip-case respond <n>` — append a response (drives open→responded)
- `/wip-case comment <n>` — add a comment (any state; no transition)
- `/wip-case close <n>` — close without implementing (won't-fix / not-an-issue / deferred)
- `/wip-case implement <n>` — apply the proposed patch, then close as implemented
- `/wip-case reopen <n>` — bring a closed/implemented case back to open (regression / disproven close)

## Prerequisites

A session ID (see YAC Reporting in CLAUDE.md).

## The served KB client (one-time, self-refreshing)

Fetched from the running instance — version-matched, no FR-YAC dependency. Inputs
are the instance URL + your API key (both from `.claude/kb.json`):

```bash
curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
  "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/apps/kb/server-api/kb-client/install" | sh
```

That materializes the bundle into `~/.cache/wip-kb-client/` and writes the stable
`kbc` shim to `~/.local/bin/kbc`. Everything below runs through `kbc`; the runner
behind it self-refreshes when the instance's bundle digest changes and restores a
missing shim on every invocation:

```bash
kbc case-fetch.py …    # reads
kbc kb-write.py …      # writes
```

If `kbc` does not resolve, re-run the install one-liner — that is the whole
recovery. (The bootstrap curl stays long-form deliberately: it runs before `kbc`
can exist.)

**One write surface.** All writes are `kb-write.py <TYPE> …` → the gateway's single
`POST /write/:type`. The gateway allocates the `case_number` + claims the `CASE-<n>`
synonym, scoped `CASE-<n>#<seq>` for responses, and persists edges. Status-transition
*validity* is enforced here in the playbook (compose only a legal transition); the
gateway is pure persistence.

Legal transitions: `open → {responded, closed, implemented}`,
`responded → {closed, implemented}`, and the reopen path
`closed → open` / `implemented → open` — so "terminal" means "resting
state", not "sealed forever": a case whose fix regressed, or that was
closed on a premise later disproven, comes back as the SAME case with its
thread intact instead of a duplicate filing.

---

## `/wip-case file`

1. **Time:** `date '+%Y-%m-%d %H:%M'`.
2. **Compose `case.md`** — frontmatter keys are CASE_RECORD fields; the markdown
   after the fence becomes the case body:

   ```markdown
   ---
   title: <short case title>
   authored_by: <your session ID>
   filed_by: <your session ID>
   doc_status: published
   status: open
   type: <bug | question | request | platform-gap>
   severity: <blocks-me | annoying | fyi>
   component: <wip-client | document-store | registry | scaffold | mcp-server | wip-react | wip-proxy | wip-auth | reporting-sync | other>
   app: <your app name, or "backend">
   topics: [<pick 1–4 from KB_TOPIC>]
   target_yac: <FRanC | BE-YAC | any>
   ---

   ## Problem
   <what happened, with evidence — errors, behaviour, missing functionality;
   specific enough for a YAC with no knowledge of your app.>

   ## Expected
   <what should have happened.>

   ## Workaround
   <what you're doing meanwhile, or "None" if blocked.>

   ## Peter's Take
   <verbatim, only if Peter gave a comment with /wip-case file; else omit.>
   ```

   **On `topics`** — the subject-matter tags the KB's Topic facet navigates by,
   drawn from the `KB_TOPIC` vocabulary (browse it as the Topic facet in KB
   search; the hierarchy rolls up, so tagging a leaf also surfaces the case
   under its parent). Pick 1–4 that describe what the case is *about*, which is
   not the same as which component it was filed against: a case about identity
   hashing filed on `document-store` wants `identity-hashing`, and that is
   exactly the tag no facet can infer for you.

   Omitting the line is safe. The gateway fills a baseline from `component` and
   `app` on write, so a case is never invisible to topic navigation. It resolves
   those through the vocabulary's own term aliases, so it only ever produces
   tags naming the component and app you already gave it — anything about the
   *subject* has to come from you. Whatever you write wins outright; the
   fallback runs only when the field is absent or empty.

3. **File it** (the gateway mints `case_number` + the `CASE-<n>` synonym; link any
   related cases as REFERENCES edges):

   ```bash
   kbc kb-write.py CASE_RECORD case.md --edge REFERENCES:CASE_RECORD:12 --edge REFERENCES:CASE_RECORD:340
   # -> created CASE-<n> (<document_id>)  edges: REFERENCES→12=linked, …
   ```

4. **Confirm:** tell Peter the case number + document_id.

---

## `/wip-case list`

```bash
kbc case-fetch.py list --status open,responded
# facets: --status (comma list) --filed-by --severity --type --component --app  --format table|json
```

APP-YACs: filter to your app (`--app <name>`) or cases you filed (`--filed-by <id>`).
BE-YACs: list all. If none, say "No cases" and stop.

---

## `/wip-case read <n>`

```bash
kbc case-fetch.py case <n>                    # body + full response thread (default: view=both)
kbc case-fetch.py case <n> --view case        # case body only
kbc case-fetch.py case <n> --view responses   # the response thread only
kbc case-fetch.py case <n> --response latest  # just the latest response
kbc case-fetch.py case <n> --response <seq>   # a specific response (404 if absent)
kbc case-fetch.py case <n> --format json      # raw gateway payload
```

Prints the case body followed by its responses (CASE_RESPONSE docs, seq-ordered,
active-only) — one gateway read, no separate fetch. Backed by
`GET /cases/:n?view=both|case|responses[&response=latest|<seq>]`. If the case (or an
explicit `--response <seq>`) isn't found, tell Peter and stop.

---

## `/wip-case respond <n>`

**Analyse before responding — do not jump to implementation.**
1. Understand the root cause from the source, not the symptom.
2. Don't assume the filer's proposed fix is correct — check it against the platform
   (validation rules, identity scoping, bulk contracts, edge cases). Propose a better
   one if warranted, and say why.
3. If the proposed fix IS right, say so and show the analysis.
   (CASE-50 was implemented blindly and broke; CASE-36 was analysed properly. Be CASE-36.)

Then, two writes — the response doc + the status transition:

```bash
# 1) the response (compose response.md: body markdown; frontmatter sets the fields)
#    response.md frontmatter: case_number: <n> / response_kind: respond / author: <id> / doc_status: published
kbc kb-write.py CASE_RESPONSE response.md --edge RESPONDS_TO:CASE_RECORD:<n>
# -> created CASE-<n>#<seq>  edges: RESPONDS_TO→<n>=linked

# 2) drive open → responded (only if currently open)
kbc kb-write.py CASE_RECORD --patch status=responded --match case_number=<n>
```

Confirm the case number + new status to Peter.

---

## `/wip-case comment <n>`

A comment is a CASE_RESPONSE with `response_kind: comment` and **no** status change
(legal in any state, including terminal):

```bash
kbc kb-write.py CASE_RESPONSE comment.md --edge RESPONDS_TO:CASE_RECORD:<n>
# comment.md frontmatter: case_number: <n> / response_kind: comment / author: <id> / doc_status: published
```

---

## `/wip-case close <n>`

Close without implementing (won't-fix / not-an-issue / deferred / handled manually).
Compose the resolution rationale, then response + transition:

```bash
kbc kb-write.py CASE_RESPONSE close.md --edge RESPONDS_TO:CASE_RECORD:<n>
#   close.md frontmatter: case_number: <n> / response_kind: close / author: <id> / doc_status: published
kbc kb-write.py CASE_RECORD --patch status=closed --match case_number=<n>
```

Terminal. Tell Peter it's closed and why.

---

## `/wip-case implement <n>`

The "do the work" command. Read the case (`case-fetch.py case <n>`); if it has no
proposed fix, tell Peter to `/wip-case respond` first and stop.

1. **Verify the proposed fix before touching code** — does the analysis convince you?
   Has the target code changed since the response? Side effects on other callers/tests?
   If anything is wrong, respond with your findings instead of implementing a fix you
   don't trust.
2. **Apply each change**; if quoted "current text" no longer matches, flag and skip it.
3. **Show the diff** (`git diff`) and let Peter review before committing.
4. **Record + close:**

   ```bash
   kbc kb-write.py CASE_RESPONSE implement.md --edge RESPONDS_TO:CASE_RECORD:<n>
   #   implement.md frontmatter: case_number: <n> / response_kind: implement / author: <id> / doc_status: published
   kbc kb-write.py CASE_RECORD --patch status=implemented --match case_number=<n>
   ```

Terminal. Tell Peter what was applied and that the case is implemented.

---

## `/wip-case reopen <n>`

Bring a `closed` or `implemented` case back to `open` — for a fix that
regressed, or a close whose premise was later disproven. Reopen the SAME
case rather than filing a duplicate: the thread's history is the context
the next responder needs. Always say WHY in a comment — a bare status flip
strands the next reader.

```bash
kbc kb-write.py CASE_RESPONSE reopen.md --edge RESPONDS_TO:CASE_RECORD:<n>
#   reopen.md frontmatter: case_number: <n> / response_kind: comment / author: <id> / doc_status: published
#   body: why this is coming back (what regressed / which premise fell)
kbc kb-write.py CASE_RECORD --patch status=open --match case_number=<n>
```

Only from `closed` or `implemented` (an `open`/`responded` case has nothing
to reopen). The case then walks the normal machine again.

---

## Discovering what you can write — the doc-type manifest

Before writing an unfamiliar type, ask the instance what it accepts. The manifest is
generated from the templates themselves, so it can never drift from what the gateway
will take:

```bash
kbc kb-write.py --list                      # every writable type, across all namespaces
kbc kb-write.py --list --namespace library  # scope to one namespace
kbc kb-write.py --list --format json
```

Each row gives the type, its **home namespace**, its write mode (`mint` = the gateway
allocates a number + synonym; `natural` = upsert by identity fields), the synonym
prefix, and the identity fields. Unscoped it spans the corpus **and** the library —
that is how `LIBRARY_DOC` (namespace `library`) is discoverable at all (CASE-701).

---

## Fetching a doc by its handle

Mint types carry a human handle — `CASE-<n>`, `FIRESIDE-<n>`, `LESSON-<n>`,
`DECISION-<n>`, `PAPER-<n>` — and the read verbs accept the number directly:

```bash
kbc case-fetch.py case 692           # cases by number
kbc case-fetch.py fireside 21        # a fireside by number …
kbc case-fetch.py fireside FIRESIDE-21   # … or by its full synonym, or a document_id
kbc case-fetch.py edges FIRESIDE-21  # every edge touching it
```

`case-fetch.py fireside list` shows the `#` column so you can find the number in the
first place. For types without a bespoke verb, filter on the number field instead:
`read LESSON --filter lesson_number=12` (CASE-703).

---

## Full-text search — find by content (CASE-707)

Every verb above filters on values you already know. This is the "which docs mention
X" surface, across the **whole corpus** — `kb` and `library` are searched as one and
merged. Backed by `GET /search`, which fronts the platform's reporting-sync FTS
(Postgres tsvector, ranked, with snippets).

```bash
kbc case-fetch.py search "reporting-sync"
kbc case-fetch.py search "flag dispatch" --type CASE_RECORD
kbc case-fetch.py search "restore" --mode substring --limit 50
#   --type   restrict to one doc type (CASE_RECORD, FIRESIDE, LESSON, DOCUMENT, …)
#   --mode   auto (default) | fts | substring — auto falls back to substring when
#            FTS finds nothing
#   --limit  max hits (default 25, cap 100); output flags when it truncated
#   --format table|json
```

Results carry type, relevance score, title, a snippet, and the document_id — follow up
with `case <n>` / `fireside <n>` / `read <TYPE>` to pull the full doc. Exit 1 when
nothing matched.

**Use this instead of dropping to reporting SQL.** Raw SQL bypasses the gateway (and
targets whichever instance your env points at); this stays on the same gateway as every
other verb.

---

## Reading any KB type — generic typed read (CASE-683)

Read/write parity: every doc type `kb-write.py` can write is readable through one
generic verb, symmetric to the single `POST /write/:type` write surface. Backed by
`GET /read/:type`. Use this for the writable types that have no bespoke read verb
(`YAC_MEMORY`, `LESSON`, `DESIGN_DECISION`, `GIT_STATS_SNAPSHOT`,
`BOOTSTRAP_RECORD`, `DOCUMENT`, …); the dedicated verbs above (`case`, `list`,
`fireside`, `library`, `journey`) are richer where they exist.

```bash
kbc case-fetch.py read <TYPE> [--filter KEY=VALUE …]
#   TYPE        — the doc type value, e.g. YAC_MEMORY, LESSON, DESIGN_DECISION, DOCUMENT
#   --filter    — repeatable; each KEY=VALUE is an eq-match on data.KEY, so a type's
#                 identity fields filter for free
#   --namespace — override the type's home namespace (default: its home)
#   --page / --page-size — paginate (default page 1, 50 rows/page, cap 100)
#   --format table|json  — default table

kbc case-fetch.py read LESSON --filter owner=FRanC
kbc case-fetch.py read GIT_STATS_SNAPSHOT --filter repo=WIP-KB --filter snapshot_date=2026-07-18
kbc case-fetch.py read YAC_MEMORY --format json
```

Exit 1 if the type is unknown or has no read route; exit 2 on transport failure.

---

## Edges — attach and inspect cross-links (CASE-630)

Cross-links live in the graph, not in prose. A response that concerns another
case gets a REFERENCES edge (CASE_RESPONSE is an allowed source since CASE-630).

**Attach an edge between two EXISTING docs** — the sanctioned retry for a
failed edge intent; never re-post the document:

```bash
kbc kb-write.py EDGE REFERENCES CASE-629#1 CASE-627
# handles = Registry synonyms (CASE-<n>, CASE-<n>#<seq>, session ids) or document_ids
# idempotent: KB edge types are versioned:false, identity [source_ref, target_ref]
```

**Inspect a doc's edges** (both directions, far end labeled with its doc type):

```bash
kbc case-fetch.py edges CASE-626
kbc case-fetch.py edges CASE-626#1
```

**Failed edge intents on a doc write no longer abort the request.** The write
returns the doc result plus per-edge status (`linked` / `target_not_found` /
`error`); `kb-write.py` exits **3** when the doc persisted but an edge intent
was rejected — retry ONLY the edge with the EDGE verb above. Exit 2 remains
transport failure; a `target_not_found` intent stays exit 0 (the loaders'
converge-on-rewrite semantics).

---

## Flags — the deterministic dispatch queue (flag-for-YAC)

Peter flags a doc in the UI → a FLAG_RECORD (target_yac, flag_type) plus a
FLAGGED_FROM edge. A deterministic poller turns pending flags into sub-agent
dispatches:

```bash
kbc case-fetch.py flags --target-yac FRanC --target-type CASE_RECORD --format json
# rows carry flag_id + target.case_number — everything a dispatcher needs
```

**Lifecycle contract:** `doc_status: published` = pending dispatch (the
default filter). After dispatching, mark the flag consumed so the poller
never re-triggers it:

```bash
kbc kb-write.py FLAG_RECORD --patch doc_status=dispatched --match document_id=<flag_id>
```

(`document_id` match is the escape hatch for composite-identity types —
FLAG_RECORD's identity is `[flag_type, flagged_document]`.) Re-flagging the
same doc in the UI upserts that identity back to `published`, which re-arms
the trigger after a failed run. `--doc-status dispatched` / `all` list
consumed / every flag.
