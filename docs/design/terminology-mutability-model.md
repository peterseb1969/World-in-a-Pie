# Design Note: Terminology Mutability Model (Discussion Draft)

**Status:** Open discussion — **no decisions yet, no implementation**.
Filed 2026-04-08 by BE-YAC-20260408-2138 (continuation).
Trigger: CASE-30 (ClinTrial Explorer) — `extensible: false` on `CT_THERAPEUTIC_AREA` not enforced on the write path. Answering the case required deciding what the flags mean, and that is not settled.

**Scope of this note:** capture the discussion so far so it can be picked up later. Not a complete design. Peter has explicitly asked for more time on the alias / preferred-term story before anything lands.

---

## The problem that kicked this off

`CT_THERAPEUTIC_AREA` in the `clintrial` namespace has `extensible: false, mutable: false`. Both flags are ignored on the write path:

- `create_term` / `create_terms_bulk` never read `extensible`. Ad-hoc term creation succeeds silently.
- `update_term` never reads `mutable`. A term's canonical `value` can be rewritten in place — the most destructive operation, unguarded.
- `delete_term` *does* read `mutable`, but only to downgrade hard-delete to soft-delete (deprecation). It does not reject deletes on immutable terminologies.

So `mutable: false` today means "you can still silently rewrite any term, but if you ask to delete one we'll deprecate it instead." That is not a coherent policy — it is an accident. Any fix needs to start from a model that describes what users actually want.

## What "immutable" should mean — the working hypothesis

Peter's position, which I agree with:

> A truly immutable terminology is a dead end. Every real-world vocabulary evolves — countries get renamed, SNOMED retires concepts every release, typos get fixed. A terminology that can never change is a stuck state, not a policy. What people actually want is **controlled evolution**: the set of terms only moves forward through deliberate action, existing identities don't silently drift under downstream data, and nothing is erased accidentally.

Given that, "immutable" conflates things that should be separate. There are three operations on a terminology's term set:

| Operation | What it does | Should be governed by |
|---|---|---|
| **Add new term** | Vocabulary grows | `extensible` flag |
| **Modify existing term** | Identity / label / metadata drifts | Platform invariant (see below), not a flag |
| **Remove existing term** | Vocabulary shrinks | Always via deprecation; hard-delete governed by namespace deletion-mode |

The middle row is the one the current model gets most wrong. Inside `update_term` there is a real distinction:

- Changing the **canonical `value`** is identity-bending. It silently corrupts every document that referenced the old value. Should never be allowed on any terminology that real data points at.
- Changing **`label`, `description`, `metadata`** is cosmetic or semantic refinement. Fixing a typo, adding a translation, updating a definition. Usually fine, even on "closed" vocabularies.

So the draft invariants are:

1. **Term canonical values are stable.** `update_term` may touch label, description, metadata, but not `value`. To rename, you deprecate the old term and create a new one. This holds for every terminology, regardless of flags — it is a platform invariant, not a per-terminology setting.
2. **Deprecation is the universal retirement path.** Any term in any terminology can be moved to `inactive`. Reactivation is also always allowed. This is how curated vocabularies evolve without breaking references.
3. **Adding new terms is gated by `extensible`.** That is the only flag a terminology author needs to set. "Closed vocabulary" = `extensible: false` plus the two universal invariants.

Under this model, `mutable` has no remaining job on the terminology flag — hard-delete is already governed by the namespace deletion-mode, and the other things `mutable` claimed to guard are covered by the invariants above.

**Note on the existing `mutable` flag.** `docs/design/mutable-terminologies.md` introduced `mutable: true` for a different purpose: app-scoped, freely-editable vocabularies that live in app namespaces and allow real hard-delete. That use case is legitimate and independent of the "closed vs open" discussion here. The conflict is that *today* the same field is also being read (inconsistently) as "immutable means don't hard-delete", which mashes two unrelated concerns together. Any resolution here must preserve the mutable-terminologies feature for app-scoped vocabularies.

## The alias problem — unresolved

Peter's objection to rule 1 ("canonical values are stable"):

> We have aliases, and they are much needed. Sometimes the preferred term changes, and the previously preferred term is now an alias. A no-go in your description. Maybe it should be impossible, needs more discussion. Maybe the synonym/alias topic should not be handled via aliases, but by registry synonyms.

This is the real tension. Example:

- A term has `value: "PEDIATRICS"`, `aliases: ["PAED", "PEDS"]`.
- Editorial decision: the preferred spelling becomes `PAEDIATRICS`. The old spelling `PEDIATRICS` should become an alias of the same concept.
- Under the "canonical value is immutable" rule, that operation is forbidden — you would have to deprecate the old term, create a new one, and hope every reference either gets rewritten or resolves transparently via Registry synonyms.

Two possible resolutions, neither chosen:

**Option A — Aliases stay on Term, preferred-term swap is a first-class operation.** Relax rule 1 to allow a controlled "promote alias to value" operation that atomically swaps `value` with one of its aliases and rewrites all term-level references. Everything else on `update_term` still can't touch `value`. This keeps the current `Term.aliases` model and accepts that "canonical value" has one specific kind of allowed change.

**Option B — Move the alias/synonym story to Registry synonyms entirely.** Drop `Term.aliases`. A term has exactly one canonical value and a stable `entry_id`. Alternative spellings, historical names, and preferred-term swaps all live as Registry synonyms on the term's Registry entry. Renaming the preferred term becomes "promote a Registry synonym to the primary value, demote the old primary to a synonym" — still a value change, but at the Registry layer, with Registry's synonym resolution handling the transition.

Option B is cleaner architecturally — it pushes identity concerns into the identity authority, which matches CLAUDE.md's "Registry is the identity authority" principle. It also means there is exactly one place to look up "what is this thing called, historically and currently." But it is a bigger change: it touches the Term model, the def-store term APIs, ontology import/export, and the def-store reporting projection. It also overlaps with the broader synonym-resolution gaps tracked in `docs/design/synonym-resolution-gaps.md`.

Option A is smaller but preserves the split between "term-local aliases" and "registry synonyms" that is already slightly confusing today. It also means rule 1 needs an exception carved into it, which weakens the invariant.

**Peter's current stance (verbatim): "Maybe it should be impossible, needs more discussion. Maybe the synonym/alias topic should not be handled via aliases, but by registry synonyms. As I said, I need more time for this."**

No decision. This note exists so the discussion can resume from the right place.

## Rabbit holes named but not entered

These came up in the discussion and are explicitly out of scope for this note. Each needs its own treatment before anything implementation-level can land.

- **Import pipeline bypass.** Re-importing a newer SNOMED/ICD release into an `extensible: false` terminology is an add operation. The import pipeline is privileged and deliberate — it needs to bypass the flag, while ad-hoc `create_terms` via the API does not. Two code paths already exist; the policy needs to be written down.
- **Rename as a compound operation.** If the platform invariant forbids value changes (and alias/synonym renames go via Option A or B), a `rename_term` convenience API may still be desirable so callers don't have to orchestrate deprecate + create + synonym link themselves. Shape TBD.
- **Terminology versioning.** WIP versions the `Terminology` entity (schema) but not the term set as a whole. SNOMED and ICD ship as dated releases. "This document was classified against ICD-10 2023 release" has no representation today. Not part of this note, but worth flagging when the term-set lifecycle is redesigned.
- **Deprecation and ontology edges.** If a term is deprecated, its `is_a` / `part_of` edges stay but it becomes unreachable via discovery while remaining resolvable by ID. Worth confirming that is the intended behaviour — currently it is implicit.
- **`CT_THERAPEUTIC_AREA` right now.** Whatever lands, it cannot retroactively freeze the clintrial terminology the app team is actively extending. Any enforcement patch must be preceded by flipping `CT_THERAPEUTIC_AREA` to `extensible: true` (and possibly `mutable: true` under the app-scoped model) so the ClinTrial Explorer team is not blocked.

## Why we are not fixing CASE-30 yet

CASE-30 asked for four small guards in `terminology_service.py`. Those guards are well-scoped and the patch is small, but they assume the current flag model is coherent. It is not. Landing the patch now means:

- Enforcing `extensible: false` and `mutable: false` as they stand, which still allows `update_term` to rewrite `value` freely (the most destructive path, and the one the case did not even mention).
- Blocking the preferred-term-rename use case Peter flagged, without a plan for how to support it.
- Locking in a flag semantics that the next design pass will need to rip out.

A partial fix that hardens the incoherent model is worse than the current state, because it makes the invariants *look* enforced while still leaving the real identity holes open. Better to settle the model first.

## What this note is for

- A handoff point if the next session needs to pick this up.
- A record of Peter's position on "truly immutable is a dead end" and the alias tension, so it is not lost.
- A list of the open questions that must be answered before any code lands on CASE-30:
  1. Is rule 1 ("canonical values are stable") an absolute invariant, or is preferred-term swap a permitted exception?
  2. Does the alias/synonym story move entirely to Registry synonyms (Option B), or do term-local aliases stay and Option A is adopted?
  3. Is `mutable` retained only for app-scoped hard-deletable vocabularies (per `mutable-terminologies.md`) and removed as a closed-vocabulary signal?
  4. What does the import pipeline bypass look like?
  5. How does `CT_THERAPEUTIC_AREA` get unblocked before enforcement lands?

**Do not implement until these are answered.**
