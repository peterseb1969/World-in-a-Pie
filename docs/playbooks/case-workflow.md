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

A session ID in `.claude/.session-id` (minted by `/wip-setup` or `/wip-wake`; see YAC Reporting in CLAUDE.md).

**Attribution is filled by the client — do not type it.** `kb-write.py` fills `filed_by` / `authored_by` on a `CASE_RECORD` and `author` on a `CASE_RESPONSE` from `.claude/.session-id`. Leave those keys out of the frontmatter. A value you do type must be a session ID, or the write is refused; an unexpanded `$(cat …)` once reached the KB as the literal attribution of a case.

## The served KB client (one-time, self-refreshing)

Fetched from the running instance — version-matched, no FR-YAC dependency. Inputs
are the instance URL + your API key (both from `.claude/kb.json`):

```bash
curl -fsSk -H "X-API-Key: $(cat "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')")" \
  "$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')/server-api/kb-client/install" | sh
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

**A patch names exactly one document.** `--patch … --match k=v` changes the one
document its `--match` fields name together; `--match` repeats, and every field is
sent. A match that several documents satisfy is refused with 409 (`ambiguous_match`),
never applied to whichever came first. `case_number` alone names a `CASE_RECORD`; a
response needs both fields (`--match case_number=<n> --match response_seq=<seq>`), or
its `document_id`.

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
   doc_status: published
   status: open
   type: <bug | gap | docs | design | process | chore | question>
   severity: <security | blocks-me | wrong-result | degraded | annoying | cosmetic | fyi>
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

   **On `type` and `severity`** — both are drawn from vocabularies (`CASE_TYPE`,
   `CASE_SEVERITY`), and a value outside them is REJECTED. Pick the type by the
   work the case asks for:

   | type | when |
   |---|---|
   | `bug` | something does the wrong thing |
   | `gap` | something needed does not exist yet (capability, endpoint, feature) |
   | `docs` | documentation is wrong, missing or obsolete |
   | `design` | a decision is needed before anyone builds |
   | `process` | how the agents work: rules, drift, coordination |
   | `chore` | clean-up, audit or investigation that changes no behaviour |
   | `question` | needs an answer, not a change |

   Pick the severity by what happens if nothing is done, worst first:

   | severity | when |
   |---|---|
   | `security` | data or access reachable by someone who should not have it, a leaked secret, an auth gap |
   | `blocks-me` | work stops; no workaround |
   | `wrong-result` | wrong or lost data, a silent failure — it looks fine and is not |
   | `degraded` | works, but slowly, unreliably, or only with a workaround |
   | `annoying` | friction or extra effort; nothing is actually wrong |
   | `cosmetic` | wording, layout, appearance |
   | `fyi` | no action expected |

   A silent failure is `wrong-result`, not `annoying`: being unnoticed makes it
   worse, not milder. Older spellings (`request`, `platform-gap`, `minor`, …) are
   accepted as aliases of the term they meant; write the current value.

   **On `topics`** — the subject-matter tags the KB's Topic facet navigates by,
   drawn from the `KB_TOPIC` vocabulary. **List it before you write:**

   ```bash
   kbc case-fetch.py topics          # the whole vocabulary, indented as its hierarchy
   ```

   The field is validated and an unknown value is REJECTED, so read the list
   rather than inventing a tag — the vocabulary is smaller than the concepts you
   might name for it. The hierarchy rolls up, so tagging a leaf also surfaces the
   case under its parent. Pick 1–4 that describe what the case is *about*, which is
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
kbc case-fetch.py case CASE-<n>#<seq>         # a response by its handle (= --response <seq>)
```

`<n>` may be written as the KB prints it: `1179` and `CASE-1179` (any case, or
`#1179`) name the same case. The same holds wherever a write takes a case number:
`case_number:` in frontmatter, `--match case_number=CASE-1179`, and an edge's target
key (`--edge RESPONDS_TO:CASE_RECORD:CASE-1179`).

Prints the case body followed by its responses (CASE_RESPONSE docs, seq-ordered,
active-only) — one gateway read, no separate fetch. Backed by
`GET /cases/:n?view=both|case|responses[&response=latest|<seq>]`. If the case (or an
explicit `--response <seq>`) isn't found, tell Peter and stop.

---

## Claims — say you are working on a case before you start

A claim records that you are working on a case for one kind of work (`respond`,
`implement`, `fix`, `review`), so other agents (the review queue included) see it
and do not start the same work. `case <n>` shows the case's claims first
(`> **claimed:** …`), and `list` has a **Claimed** column: **check before starting**.

```bash
kbc kb-write.py CLAIM acquire <n> <kind>   # before starting; refused (exit 1) if someone holds it
kbc kb-write.py CLAIM renew   <n> <kind>   # extend the lease on long work
kbc kb-write.py CLAIM park    <n> <kind> --note "waiting on the decision in CASE-<m>"
kbc kb-write.py CLAIM release <n> <kind>   # when done, failed or abandoned
```

- The holder is your session (`.claude/.session-id`); a task runner passes `--holder <task-id>`.
- A refused acquire names who holds the case. Do not work around it: ask them, or wait.
- An **active** claim has a lease (60 minutes unless the kind's `CLAIM_RULE` says
  otherwise). Renew on long work; an unrenewed lease runs out and the case is free.
- A **parked** claim has no lease: use it when you stop to wait for a decision, so
  the case stays yours. `acquire` again to resume it.
- Each kind's `CLAIM_RULE` (a config document) sets how many may hold it at once and
  which kinds exclude each other. Without one: one holder, no exclusions.
- Release when you are done. A posted response does not release a claim by itself.

---

## `/wip-case respond <n>`

**Analyse before responding — do not jump to implementation.**
1. Understand the root cause from the source, not the symptom.
2. Don't assume the filer's proposed fix is correct — check it against the platform
   (validation rules, identity scoping, bulk contracts, edge cases). Propose a better
   one if warranted, and say why.
3. If the proposed fix IS right, say so and show the analysis.
   (CASE-50 was implemented blindly and broke; CASE-36 was analysed properly. Be CASE-36.)
4. Take the whole view — the responder decides whether the case asks for the right thing:
   - **Is the scope right?** Grep for the same defect beyond the cited lines; check
     whether part of it is already fixed.
   - **Does the fix fit the repo?** Release notes, design docs, how sibling code does it.
     A fix that contradicts a recorded convention is wrong, however small.
   - **Does it apply beyond the request?** Name every extra site with `file:line`. Do
     not widen on a hunch.
5. **Stay proportionate.** Anything bigger than the case asked for — a rewrite or
   redesign, more sites or another component, a different approach, a change to a shared
   contract or to stored data — is not the recommendation. Give the fix as filed (or say
   why it is wrong), and put the bigger change as a question for the project owner, with
   the evidence, the options and their cost.

**Claim it first** (see Claims above): `kbc kb-write.py CLAIM acquire <n> respond`. A
refused acquire means someone else is responding: stop. Release when the response is
posted, or when you give up.

**The response body** — every respond, attended or unattended, has this shape:

```markdown
**Bottom line:** <verdict in one sentence> · **Root cause:** <one sentence> · **Recommendation:** <implement as filed | implement with the correction below | needs a live check first | question for the project owner | cannot form a confident finding>

## Files in scope

- `path/from/repo/root.ts` — change
- `another/file.py`, `its/test.py` — change, **shared contract** (served schema)
- `file/only/read.ts` — read

## Scope

<as filed | widened | narrowed | different fix — and the evidence: the grep, the
convention, the file:line of every site beyond the request>

## Analysis

<root cause from the source, file:line per claim; whether the filer's fix is right>

## Verified vs not

<what you read directly, versus inferred or not checkable>

## Questions

<optional: a change bigger than the case asked for, put to the project owner with
evidence, options and cost — instead of recommending it>
```

- **Length:** about 600 words, 1,000 at most. Go longer only with a stated reason
  (several independent findings). Don't restate the case.
- **Files in scope** comes right after the bottom line. One bullet per line: backticked
  repo-relative paths, then a dash and `change` or `read`, then any note. Say so when a
  change touches a shared contract (an API model, a served schema, a template). A file
  in another repo is named with that repo, as prose, not as a bullet.
- `kb-write.py` copies the list into the response's `files_change` / `files_read` fields,
  which is what a scheduler reads to decide whether two cases can be worked in parallel.
  It warns (it does not refuse) when a respond has no such section, or a bullet in it
  that it cannot read.
- **Scope** is a judgement, not a file list: whether the case asks for the right thing.
  Files in scope lists what the recommended fix touches, for the scheduler; a widened
  scope that is only a question lists no files there.
- **Questions** is optional. When it holds the main point, the recommendation is
  *question for the project owner*, and nothing is implemented until the owner answers.

Then, two writes — the response doc + the status transition:

```bash
# 1) the response (compose response.md: body markdown; frontmatter sets the fields)
#    response.md frontmatter: case_number: <n> / response_kind: respond / doc_status: published
kbc kb-write.py CASE_RESPONSE response.md --edge RESPONDS_TO:CASE_RECORD:<n>
# -> created CASE-<n>#<seq>  edges: RESPONDS_TO→<n>=linked

# 2) drive open → responded (only if currently open)
kbc kb-write.py CASE_RECORD --patch status=responded --match case_number=<n>

# 3) let the case go
kbc kb-write.py CLAIM release <n> respond
```

Confirm the case number + new status to Peter.

---

## `/wip-case comment <n>`

A comment is a CASE_RESPONSE with `response_kind: comment` and **no** status change
(legal in any state, including terminal):

```bash
kbc kb-write.py CASE_RESPONSE comment.md --edge RESPONDS_TO:CASE_RECORD:<n>
# comment.md frontmatter: case_number: <n> / response_kind: comment / doc_status: published
```

---

## `/wip-case close <n>`

Close without implementing (won't-fix / not-an-issue / deferred / handled manually).
Compose the resolution rationale, then response + transition:

```bash
kbc kb-write.py CASE_RESPONSE close.md --edge RESPONDS_TO:CASE_RECORD:<n>
#   close.md frontmatter: case_number: <n> / response_kind: close / doc_status: published
kbc kb-write.py CASE_RECORD --patch status=closed --match case_number=<n>
```

Terminal. Tell Peter it's closed and why.

---

## `/wip-case implement <n>`

The "do the work" command. Read the case (`case-fetch.py case <n>`); if it has no
proposed fix, tell Peter to `/wip-case respond` first and stop. Claim it before you
start (`kbc kb-write.py CLAIM acquire <n> implement`) and release it once the case is
implemented, or when you stop without finishing (`park` it instead when you stop to
wait for a decision).

1. **Verify the proposed fix before touching code** — does the analysis convince you?
   Has the target code changed since the response? Side effects on other callers/tests?
   If anything is wrong, respond with your findings instead of implementing a fix you
   don't trust. Stay in the response's scope: when a finding challenges your
   instructions (the fix contradicts a convention, the same defect sits elsewhere), stop
   and say so rather than widening the change yourself.
2. **Apply each change**; if quoted "current text" no longer matches, flag and skip it.
3. **Show the diff** (`git diff`) and let Peter review before committing.
4. **Push, then record + close.** The implement response is where a reader finds the
   fix, so write it only after the push, and cite each commit hash as it is on the
   remote (`git log --format='%h %s' <remote>/develop`). If the push was rejected and
   you rebased, every hash changed. `kb-write.py` refuses an implement response that
   cites a commit present in your clone but on no remote branch; a hash it cannot find
   at all, or no hash, only draws a warning. Run it from the repository the commits
   live in, or pass `--hash-repo PATH`. `--no-hash-check` skips the check for a fix
   that is not a pushed commit.

   ```bash
   kbc kb-write.py CASE_RESPONSE implement.md --edge RESPONDS_TO:CASE_RECORD:<n>
   #   implement.md frontmatter: case_number: <n> / response_kind: implement / doc_status: published
   kbc kb-write.py CASE_RECORD --patch status=implemented --match case_number=<n>
   kbc kb-write.py CLAIM release <n> implement
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
#   reopen.md frontmatter: case_number: <n> / response_kind: comment / doc_status: published
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
#   --namespace  search another knowledge base (default: the configured one); the
#                library is always included
#   --project    only documents tagged with this project (any alias of its KB_APP term)
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

---

## Projects and knowledge bases (DECISION-1)

A **project** is a `KB_APP` term inside a knowledge base. Its documents are the ones
whose `app` field names it: cases, decisions, lessons, firesides, sessions, memories,
mirrored documents. Any alias of the term works wherever a project is named. An optional
`PROJECT` profile document carries its scope, repository ids and a tooling archive.

```bash
kbc case-fetch.py brief                       # the configured project's context in one read
kbc case-fetch.py brief <project> --tooling DIR   # another project; also save its archive (never unpacked)
kbc case-fetch.py search "<terms>" --project <project>
kbc case-fetch.py read CASE_RECORD --filter app=<project>
```

- **Read the brief at session start** in a project's repository. It lists open cases and
  the newest decisions, lessons, firesides, sessions, memories and documents as handles
  and titles; each is one read away. Untagged documents are not listed, so an empty
  section means "nothing tagged", not "nothing exists".
- **Tagging is automatic** when `.claude/kb.json` has `"project": "<project>"`: every
  write of a type with an `app` field is tagged with it unless the document names its
  own `app` (an explicit value wins). Without the key, set `app:` in the frontmatter.
- **A new project needs its `KB_APP` term first.** An administrator adds it on the
  app's Settings → Vocabulary page; writing an unknown project is rejected.
- **Profile:** `kbc kb-write.py PROJECT --json '{"app": …, "title": …, "body": "<scope>",
  "repo_ids": […], "authored_by": …, "doc_status": "published"}'`. Its identity is `app`,
  so writing again updates the same profile.
- **Tooling archive:** `kbc kb-write.py PROJECT --tooling ./tooling.zip --match app=<project>`
  attaches a `.zip`, `.tar.gz` or `.tgz` (up to 100 MB) to an existing profile, or add
  `--tooling PATH` to the profile write itself. Without `--match` it uses the configured
  project. A new archive replaces the old one, which stays in WIP unreferenced. It runs
  with your key, so WIP's write permission on the knowledge base decides who may.

A **knowledge base** is a namespace seeded with the full KB model, with its own
documents, numbering, vocabularies and relationships; links cannot cross between
knowledge bases. Making one takes two steps:

1. An operator reserves the namespace for the KB app. For a KB deployed by
   wip-deploy, on the install's host: `wip-deploy app-namespace add wip-kb <name>
   --name <install>` (every later apply keeps it). For an attached KB: widen the KB's
   API key with `reserve` on the namespace in the Console.
2. `kbc kb-namespace.py create <name>`, run with a key holding **admin on the main
   knowledge base** (`kb`), creates and seeds it. The KB seeds with its own key, so it
   can always see what it created. Run before step 1, it is refused (409
   `not_reserved`, naming step 1) and nothing is created; a key with only write is
   refused with 403.

Never copy another key into the app's key file to widen it; the next apply
re-issues that file, and two keys sharing one value resolve inconsistently.

`"namespace": "<name>"` in `.claude/kb.json` points the whole
client at it; `search`, `read`, `brief` and `topics` also take `--namespace` for a
single command.
