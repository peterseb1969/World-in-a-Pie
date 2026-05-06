# Doc-Review Workflow Playbook

Full handler reference for the `/doc-review` slash command. The slash command stub at `.claude/commands/doc-review.md` performs the directory pre-flight, then reads this file and dispatches based on `$ARGUMENTS`.

By the time you are reading this, `yac-discussions/` is known to exist. Do not re-check.

> **Source of truth.** The canonical case-frontmatter spec lives in `templates/doc-yac/CLAUDE.md` §6 (FR-YAC's territory; the gene-pool source for DOC-YAC's CLAUDE.md). When the format evolves, FRanC updates §6; this playbook reads from it. If anything in §6 contradicts what's below, §6 wins.

DOC-YAC files two case types you'll see here:

- `type: doc-audit` — full questionnaire walk on a surviving doc; carries a `severity` field.
- `type: doc-deletion` — short-form case recommending `git rm`; **no `severity` field by design** (per CASE-234 — deletion is a placement decision, not a content-quality judgment).

## Subcommands

- `/doc-review` — review all open doc-audit / doc-deletion cases
- `/doc-review <number>` — review a single case (e.g., `/doc-review 235`)

---

## Handling `/doc-review` (full queue)

### 1. Find open doc-review cases

Filter by frontmatter `type:`, not by filename pattern. Current cases use `CASE-NN-<status>-<slug>.md` per `case-workflow.md`; legacy `CASE-*-doc-*.md` globs match nothing.

```bash
for f in yac-discussions/CASE-*-open-*.md; do
  head -20 "$f" | grep -qE '^type: (doc-audit|doc-deletion)$' && echo "$f"
done
```

For re-reviews, also include responded cases with a `## Comment` section dated *after* your last `## Response`:

```bash
for f in yac-discussions/CASE-*-responded-*.md; do
  head -20 "$f" | grep -qE '^type: (doc-audit|doc-deletion)$' && echo "$f"
done
```

For each candidate from the second list, verify the most recent `## Comment — ...` block is dated after the last `## Response — ...` block before including it.

If neither pass yields cases, say "No doc-review cases needing attention" and stop.

### 2. Synthesis-group preflight

Before processing the queue: if any case has `proposed_action: merge-into-X`, grep for sister cases sharing a `synthesis_group:` slug and bundle them.

```bash
grep -l 'synthesis_group: <slug>' yac-discussions/CASE-*.md
```

When N cases feed one synthesis target, read all N before proposing the merged replacement. Synthesizing once across the group beats writing N independent "merge into X" responses that don't see each other.

### 3. Deletion-sweep pre-flight (mandatory when N ≥ 3 doc-deletion cases)

If the queue has three or more `type: doc-deletion` cases scheduled for the same PR/sweep, run a **single batched cross-cutting grep** before presenting the queue. The per-case §A.2 grep is correct at the unit level, but a sweep needs the cross-cutting picture *before* any destructive op so deletions in queue order don't silently break code that forces-reads a later target.

This step exists because the per-case discipline misses cross-cutting dependencies. CASE-281 was filed after a 50-doc deletion sweep where 7 of 50 turned out to be forced-read by `.claude/commands/` stubs, scaffold scripts, CLAUDE.md self-refs, and source-code citations. In-queue-order deletion would have silently broken `/roadmap`, `/resume`, `/case`, and fresh app project setup.

**Three buckets:**

- **Clean delete** — no forced-read references found anywhere. Safe to proceed in the sweep PR.
- **Forced-read defer** — referenced from `.claude/commands/` stubs, `scripts/setup-*.sh` heredocs, `CLAUDE.md` self-refs, source code (`components/`, `libs/`), or test docstrings. These need a companion PR that creates replacements, updates references, or decides the slash-command fate. **Do not delete in the same PR as the clean ones.**
- **Index-only** — referenced only by `README.md` Documentation tables or dev-guide doc-tables. Anticipated by the audit's post-consolidation index PR; can be bundled there rather than blocking the clean sweep.

**Recipe — single batched grep across all `target_doc:` paths in the deletion-case set:**

```bash
# Collect deletion targets from the deletion-case set into a tempfile
# (avoids bash array — portable to macOS bash 3.2 which lacks mapfile).
TARGETS_FILE=$(mktemp)
for f in yac-discussions/CASE-*-open-*.md; do
  head -20 "$f" | grep -q '^type: doc-deletion$' || continue
  awk '/^target_doc:/ {print $2}' "$f"
done > "$TARGETS_FILE"

echo "=== Found $(wc -l < "$TARGETS_FILE") deletion targets ==="

# Cross-cutting search per target. Skip the target itself in the matches.
while IFS= read -r path; do
  [ -z "$path" ] && continue
  base=$(basename "$path")
  echo "=== $path ==="
  grep -rln \
    --include="*.md" --include="*.sh" --include="*.py" --include="*.yaml" \
    --include="*.ts" --include="*.tsx" \
    -e "$base" -e "$path" \
    .claude/commands/ docs/ scripts/ CLAUDE.md \
    components/ libs/ apps/ 2>/dev/null \
    | grep -v "^$path$"
done < "$TARGETS_FILE"

rm -f "$TARGETS_FILE"
```

Categorize each target into one of the three buckets, then **report the bucketing to Peter before any destructive op.** Format:

```
=== Deletion-sweep pre-flight (N=12 deletions) ===
Clean (8): docs/old-X.md, docs/legacy-Y.md, ...
Forced-read defer (3):
  - docs/playbooks/backend/case-workflow.md → .claude/commands/case.md (slash-command stub)
  - docs/architecture.md → CLAUDE.md §3, scripts/setup-backend-agent.sh heredoc
  - docs/dev-delete.md → scripts/create-app-project.sh (copies into every new app)
Index-only (1): docs/k8s-installation_log.md → README.md Documentation table
=== 3 targets need carve-out before sweep can proceed cleanly. ===
```

Peter approves the carve. The sweep PR then runs against the **clean bucket only**. The forced-read defer bucket gets a companion PR (replacements + reference updates + the deletes). The index-only bucket bundles into the post-consolidation index PR.

**`forced_read_suspect:` field (DOC-YAC heuristic, advisory):** if the case frontmatter has `forced_read_suspect: <suspected-path>` (DOC-YAC's §3 Q1.5 advisory — see `templates/doc-yac/CLAUDE.md`), prioritize verifying that target first. The field is a SIGNAL, not a verification — your batched grep is what counts. The advisory exists because DOC-YAC can't grep code (its ignorance boundary), so it can only flag patterns from filenames + cross-references; verification is your job.

Skip this step when the queue has fewer than three deletion cases — the per-case §A.2 grep suffices for unit-level checks at that scale.

### 4. Present the queue

```markdown
## Doc-Review Queue

| # | Case | Document | Type | Severity | Filed by | Mode |
|---|------|----------|------|----------|----------|------|
| 1 | CASE-235 | README.md | doc-audit | needs-update | DOC-YAC-20260429-0050 | new |
| 2 | CASE-173 | k8s-installation_log.md | doc-deletion | — | DOC-YAC-20260429-0050 | new |
| 3 | CASE-259 | doc-review-workflow.md | doc-audit | needs-update | DOC-YAC-20260429-0050 | re-review |

**Total:** N cases (X audits, Y deletions, Z re-reviews). Order: re-reviews first; then `doc-audit` by severity (`needs-rewrite` → `needs-update` → `minor-issues` → `acceptable`); then `doc-deletion` cases (no severity — order by case number). Group together any cases sharing a `synthesis_group:` slug. If a deletion-sweep pre-flight (§3) carved out forced-read targets, surface that split in the queue presentation.
```

### 5. Review each case

Run the appropriate single-case procedure (see below).

### 6. Progress updates

After every 3 cases, print a one-line status:

```
Reviewed 3/12 — 2 patches proposed, 1 deletion confirmed.
```

---

## Handling `/doc-review <number>` (single case)

### 1. Read the case

Find `yac-discussions/CASE-<NN>-*.md` (e.g., `CASE-235-*`). Read the matching file. If it doesn't exist, tell Peter and stop.

### 2. Branch on case type

Read the frontmatter `type:` field:

- **`type: doc-deletion`** → §A below (short procedure).
- **`type: doc-audit`** → §B below (full procedure).

---

### §A — Handling a `doc-deletion` case

Deletion cases are short by design (FR-YAC template §4). The DOC-YAC concluded the doc has no concrete reader/task on full read; there is no questionnaire to walk.

Your job: **confirm the deletion is safe to action.**

#### A.1 Read the document

The case frontmatter has a `target_doc:` field. Read that file. If it doesn't exist (already deleted in a prior session), note it and propose closing the case as `not-an-issue`.

#### A.2 Verify nothing depends on it

Grep the repo for filename references — a doc may be forced-read by a slash-command stub, `/setup`, or `CLAUDE.md` even if no human ever opens it:

```bash
grep -rn "$(basename <target_doc> .md)" docs/ scripts/ .claude/ components/ libs/ 2>/dev/null
```

Also check git history for recency of meaningful change: `git log --oneline -- <target_doc> | head -5`.

> When processing a sweep (≥3 deletion cases in one PR), the §3 batched pre-flight already covers this grep across all targets at once. The per-case A.2 stays as the unit-level check; A.2's findings should align with §3's bucketing for the same target. If they conflict, §3's batched run wins (it sees cross-target patterns the per-case grep misses).

#### A.3 Decide

- **Safe to delete:** propose `git rm <path>`, note `v1.1.0` recovery point.
- **Load-bearing for some path DOC-YAC missed:** propose `keep` or `move-to-surface-X` instead, with rationale.

#### A.4 Respond

Skip the questionnaire-shaped response. Use:

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

### Verification
<What you checked: forced-read references, recent-change relevance, whether any current code path depends on this doc.>

### Recommendation
<delete | keep (with rationale) | move-to-surface-X>

### Proposed action
<Exact `git rm` command, or proposed alternative.>
```

Then go to §3 below (status update + rename).

---

### §B — Handling a `doc-audit` case

Full questionnaire walk. The DOC-YAC's questionnaire is defined in `templates/doc-yac/CLAUDE.md` §3.

#### B.1 Read the document under review

The case frontmatter has a `target_doc:` field with the path (relative to repo root). Find and read that file.

If the file doesn't exist (renamed or removed since the audit), note it — that's a finding in itself.

#### B.2 Evaluate each questionnaire item

**Q11 (Accuracy flags) is your primary job.** The DOC-YAC flagged claims it couldn't verify. Check each against the actual code:

- Is the described behavior correct?
- Are API examples accurate (endpoints, parameters, response formats)?
- Are configuration steps current?
- Have features been added, removed, or renamed since the doc was written?

**Q8 (Undefined references):** Determine whether the referenced concept is defined elsewhere (link needed), undocumented (doc gap — new content needed), or obvious to the intended audience (no action).

**Q9, Q10 (Contradictions):** Determine which version is correct by checking the code.

**Q1–Q7 (Audience, mode, surface, linkage):** Use your judgement. You know the codebase — is the doc pitched at the right level for its audience? Is the surface assignment right?

**Q12 (Redundant?):** Confirm or challenge the DOC-YAC's redundancy assessment.

**Q13 (Doc-specific):** Answer each one.

**Q14, Q15, Q16 (Categorization, proposed action, freshness):** Confirm or correct DOC-YAC's draft.

#### B.3 Propose a patch

If the document needs changes, write a concrete markdown patch — the actual proposed text, not a description of what to change.

```markdown
### Proposed Changes

**Change 1: [section name]**

Current text:
> [quote the current text]

Proposed text:
> [write the replacement text]

Reason: [one line — why this change]

---

**Change 2: [section name]**
...
```

If the document needs no changes (DOC-YAC was too cautious, or issues are cosmetic), say so explicitly.

If the document should be deleted (DOC-YAC misjudged "keep"), say so and explain what replaces it — convert to a deletion finding in your response.

#### B.4 Respond

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

### Accuracy Verification
<For each flag the DOC-YAC raised: confirmed inaccurate / confirmed accurate / partially accurate — with specifics>

### Findings Assessment
<Your assessment of the DOC-YAC's other findings: agree / disagree / partially agree — with reasoning>

### Proposed Patch
<The full proposed changes as described in B.3, or "No changes needed" with explanation>

### Recommendation
<One of: rewrite (with outline) | update (patch above) | minor-edit (patch above) | no-action | delete>
```

Then go to §3 below (status update + rename).

---

### 3. Update case status (both §A and §B)

Change `status: open` to `status: responded` in the frontmatter. Rename the file:

```bash
mv yac-discussions/CASE-<NN>-open-<slug>.md yac-discussions/CASE-<NN>-responded-<slug>.md
```

### 4. Confirm

Tell Peter: case ID, document, your recommendation, and the number of proposed changes (or "delete" / "keep" for deletion cases). One line.

---

## Re-reviews (round 2+)

When a case has DOC-YAC comments after your response, this is a re-review. The DOC-YAC is pushing back — take it seriously.

### How re-reviews differ from first reviews

- Read the DOC-YAC's comment carefully. It represents the fresh reader's perspective — the audience you're writing for.
- If the DOC-YAC says your patch introduces jargon: rewrite in plain language. Don't defend technical terms to a non-technical audience.
- If the DOC-YAC says your evidence was vague: cite specific files, functions, or commits. "This looks correct" is not evidence.
- If the DOC-YAC challenges a "no-action" response: reconsider. Your familiarity with the code is a bias here, not an advantage. The DOC-YAC is telling you the doc fails for its audience.

### Response format for re-reviews

Append a new `## Response` section (not a comment — this is a substantive revision):

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>) [Round N]

### Addressing DOC-YAC Pushback

**Re: [DOC-YAC's point]:** <your revised answer, with evidence>

**Re: [DOC-YAC's point]:** <your revised answer>

### Revised Patch
<Updated proposed changes incorporating feedback, or explanation of why original patch stands>
```

The `[Round N]` marker helps Peter and FRanC track the discussion depth.

---

## What you are NOT doing

- You are not making the changes. You propose. Peter reviews and decides.
- You are not re-auditing the document. The DOC-YAC did that. You are verifying findings and proposing fixes.
- You are not closing cases. Peter closes them after reviewing your proposal.

## Resolution

After Peter reviews your response, he will either:

- Accept the patch → implement it (or tell you to)
- Refine → edit the patch and implement
- Discard → close the case as no-action

Peter closes the case via `/case close <number>` or `/case implement <number>`.
