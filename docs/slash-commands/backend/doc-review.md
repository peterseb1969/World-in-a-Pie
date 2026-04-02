Review open documentation audit cases filed by a DOC-YAC. For each case, verify accuracy flags against the codebase, assess all findings, and propose concrete markdown patches.

### Usage

- `/doc-review` — review all open doc-review cases
- `/doc-review <number>` — review a single case (e.g., `/doc-review 3`)

### Prerequisites

You must have `yac-discussions/` symlinked in your project. If it doesn't exist, tell Peter.

---

### Handling `/doc-review`

#### 1. Check directory exists

```bash
test -d yac-discussions && echo "ok" || echo "missing"
```

If missing, report and stop.

#### 2. Find open doc-review cases

```bash
ls yac-discussions/CASE-*-doc-*.md 2>/dev/null
```

Read the frontmatter of each. Include cases that need your attention:
- `status: open` and `type: doc-review` — first-round reviews (not yet responded)
- `status: responded` with a `## Comment` section dated AFTER your last `## Response` — re-reviews (DOC-YAC pushed back)

If none found, say "No doc-review cases needing attention" and stop.

#### 3. Present the queue

```markdown
## Doc-Review Queue

| # | Case | Document | Severity | Filed by |
|---|------|----------|----------|----------|
| 1 | CASE-20260402-1305-doc-vision | Vision.md | needs-update | DOC-YAC-20260402-1300 | **re-review** |
| 2 | CASE-20260402-1312-doc-claude | CLAUDE.md | minor-issues | DOC-YAC-20260402-1300 | new |
| ... | ... | ... | ... | ... | ... |

**Total:** N cases (X new, Y re-reviews). Starting with re-reviews, then new by severity.
```

#### 4. Review each case

Process cases in severity order: `needs-rewrite` first, then `needs-update`, then `minor-issues`, then `acceptable`.

For each case, run the single-case procedure (see below).

#### 5. Progress updates

After every 3 cases, print a one-line status:

```
Reviewed 3/12 — 2 patches proposed, 1 no-action-needed.
```

---

### Handling `/doc-review <number>`

#### 1. Read the case

Find `yac-discussions/CASE-<NN>-*.md` (e.g., `CASE-03-*`). Read the matching file. If it doesn't exist, tell Peter and stop.

#### 2. Read the document under review

The case frontmatter has a `document:` field with the filename. Find and read that file in the current repo.

If the file doesn't exist (renamed or removed since the audit), note this — it's a finding in itself.

#### 3. Evaluate each questionnaire item

Work through the DOC-YAC's questionnaire answers:

**For accuracy flags (item 9):** This is your primary job. The DOC-YAC flagged claims it couldn't verify. Check each one against the actual code:
- Is the described behavior correct?
- Are API examples accurate (endpoints, parameters, response formats)?
- Are configuration steps current?
- Have features been added, removed, or renamed since the doc was written?

**For undefined references (item 6):** Determine whether the referenced concept is:
- Defined elsewhere (link needed)
- Not documented anywhere (doc gap — new content needed)
- Obvious to the intended audience (no action)

**For contradictions (items 7, 8):** Determine which version is correct by checking the code.

**For audience/structure issues (items 1–5):** Use your judgement. You know the codebase — is the doc pitched at the right level for its audience?

**For necessity (items 10, 11):** Confirm or challenge the DOC-YAC's assessment. You know what's actually used.

**For doc-specific questions (item 12):** Answer each one.

#### 4. Propose a patch

If the document needs changes, write a concrete markdown patch. This is the actual proposed text, not a description of what to change.

Format:

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

If the document should be deleted (redundant, obsolete), say so and explain what replaces it.

#### 5. Respond to the case

Append to the case file using the standard case response format:

```markdown
## Response — <your session ID> (<YYYY-MM-DD HH:MM>)

### Accuracy Verification
<For each flag the DOC-YAC raised: confirmed inaccurate / confirmed accurate / partially accurate — with specifics>

### Findings Assessment
<Your assessment of the DOC-YAC's other findings: agree / disagree / partially agree — with reasoning>

### Proposed Patch
<The full proposed changes as described in step 4, or "No changes needed" with explanation>

### Recommendation
<One of: rewrite (with outline), update (patch above), minor-edit (patch above), no-action, delete>
```

#### 6. Update case status

Change `status: open` to `status: responded` in the frontmatter.

#### 7. Confirm

Tell Peter: case ID, document, your recommendation, and the number of proposed changes. One line.

---

### Re-reviews (round 2+)

When a case has DOC-YAC comments after your response, this is a re-review. The DOC-YAC is pushing back — take it seriously.

#### How re-reviews differ from first reviews

- Read the DOC-YAC's comment carefully. It represents the fresh reader's perspective — the audience you're writing for.
- If the DOC-YAC says your patch introduces jargon: rewrite in plain language. Don't defend technical terms to a non-technical audience.
- If the DOC-YAC says your evidence was vague: cite specific files, functions, or commits. "This looks correct" is not evidence.
- If the DOC-YAC challenges a "no-action" response: reconsider. Your familiarity with the code is a bias here, not an advantage. The DOC-YAC is telling you the doc fails for its audience.

#### Response format for re-reviews

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

### What you are NOT doing

- You are not making the changes. You propose. Peter reviews and decides.
- You are not re-auditing the document. The DOC-YAC did that. You are verifying findings and proposing fixes.
- You are not closing cases. Peter closes them after reviewing your proposal.

### Resolution

After Peter reviews your response, he will either:
- Accept the patch → implement it (or tell you to)
- Refine → edit the patch and implement
- Discard → close the case as no-action

Peter closes the case via `/case close <number>` or `/case implement <number>`.
