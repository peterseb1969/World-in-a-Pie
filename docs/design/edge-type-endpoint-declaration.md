# Edge-Type Endpoint Declaration — Analysis and Design

**Status:** analysis v1 — current state mapped end to end; clean design proposed
**Date:** 2026-07-31
**Tracking:** CASE-830 (defect), CASE-827 (found while building the M1 archive model)
**Related shipped defects in this subsystem:** CASE-406, CASE-515, CASE-525
**Decisions taken (Peter, 2026-07-31):** archive format version bumped; D1 and
D2 ship together; `include_subtypes` does not apply to edge-type endpoints

---

## 1. Why this document exists

An edge type's allowed endpoints are declared in two places at once. Every
attempt to fix the resulting defect so far has been made from a partial map,
and each round of discussion surfaced a new load-bearing fact that changed the
recommendation — that the lists are stored as submitted, that the equivalence
helper has four call sites, that `_widen` deliberately downgrades to value
form, that an empty list means *no constraint*. None were obscure. They were
simply never mapped in one pass.

This document is that one pass: every writer, every reader, every consumer,
every form transition, read rather than inferred. The test of whether it
succeeded is that implementing from it surfaces no new load-bearing fact.

---

## 2. The two representations

An edge type (`usage: "relationship"`) carries its allowed endpoints twice:

| | Template-level | Field-level |
|---|---|---|
| Where | `source_templates` / `target_templates` on the template | `target_templates` on the `source_ref` / `target_ref` fields |
| Documented as | "Template **values** allowed as edge source" (`api_models.py:114`) | "Canonical template_ids … **resolved from values at creation**" (`field.py:170`) |
| Stored form | **as submitted** — value, id, or `ns:VALUE` (`template_service.py:562`) | **canonical ids** (resolved by `_normalize_field_references`) |
| Role | the **declaration** — what the API, console and `wip-toolkit inspect` report | the **enforcement vehicle** — what actually rejects a bad endpoint |

They are kept consistent by a hand-written check
(`_validate_relationship_template_shape`, `template_service.py:114-123`) that
compares them *by canonical entity*, so value-form and id-form of the same
template count as equal.

**The duplication is not accidental.** It is a human-readable declaration plus
a machine-enforced projection, mirror-locked. The defect is not that someone
duplicated a field; it is that the projection was materialised into storage
instead of derived, and that one write path (restore) updates the projection
without updating the declaration.

---

## 3. What the original design specified versus what shipped

`docs/design/document-relationships.md` is explicit:

- `:81-82` — "template **values** allowed as the source endpoint". Value form
  is the intended declaration form, deliberately.
- `:93-94` — "`source_ref` must resolve to a document whose **template value**
  is in the template-level `source_templates` list. Resolution goes through
  Registry (`resolve_entity_ids(bypass_cache=True)` — Principle 3)."

**That enforcement was never implemented.** Verified: `grep -rn
"source_templates" components/document-store/src` returns **nothing**. The
document-store never consults the template-level declaration at all.

What enforces today is the *generic* reference validator
(`validation_service.py:1687-1696`), which knows nothing about edge types and
simply sees a reference field with a `target_templates` constraint — the
field-level copy. The relationship-specific validation that does exist
(`document_service.py:270-300`) checks only namespace and archived status.

So the field-level mirror is how the design's intent got implemented: by
reusing the existing generic machinery rather than writing edge-type-aware
enforcement. The mirror-lock check exists to keep that reuse faithful to the
declaration.

---

## 4. Complete writer map

Every path that sets or mutates either representation:

| # | Path | Template-level | Field-level | Form written |
|---|---|---|---|---|
| W1 | `create_template` (`:562`) | assigned **verbatim** from the request | resolved to canonical by `_normalize_field_references` (`:476`) | as-submitted / canonical |
| W2 | `update_template` (new version, `:1397-1398`) | **carried forward** from the previous version (immutable) | re-resolved from request fields (`:1314`) | unchanged / canonical |
| W3 | `activate_template` (draft → active, `:2950`) | untouched | resolved at activation | — / canonical |
| W4 | `add_edge_type_endpoints` (`:1714-1731`) | widened, then **downgraded to value form** by `_value_form(canonical)` (`:1691-1699`) | mirrored from the new template-level list | value / value ⚠️ |
| W5 | Restore — remap/fresh (`remap.py:57-128`, `backup_engine._write_remapped`) | **not touched** | remapped via `template_map` | stale / canonical ⚠️ |
| W6 | Restore — id-preserving | not touched | not touched (ids preserved) | unchanged |

Two anomalies fall out of this table:

- **W4 breaks the field-level invariant.** `_widen` writes value form into the
  template-level list *and mirrors that same value form into the field-level
  copy* (`:1729-1731`). So after any endpoint widening, the field-level list —
  documented as canonical ids — holds values. The generic validator compares
  `doc.template_id not in target_templates` (`:1693`), an **id** comparison
  against what may now be a **value**. Not exercised by CASE-830's
  reproduction; flagged here as a second, independent defect of the same
  duplication. *Unverified at runtime — see §9.*
- **W5 is CASE-830.** The projection is remapped, the declaration is not.

---

## 5. Complete reader / consumer map

The structural fact that makes this tractable:

> **Only one component reads template rows straight from Mongo:**
> `document-store/services/backup_engine.py`. Everything else reads templates
> over HTTP, and every template-serving endpoint returns `TemplateResponse`,
> which is constructed in exactly one place — `_to_template_response`
> (`template_service.py:3043`, 7 call sites, no other construction site).

| Consumer | Reads | Via | Effect if the field-level list is absent |
|---|---|---|---|
| document-store reference validator (`validation_service.py:1292,1687`) | field-level | `get_template_resolved` → HTTP | **silently unconstrained** — `if target_templates:` |
| document-store relationship checks (`document_service.py:270`) | neither | — | none |
| `@wip/client` `template-to-form` (`template-to-form.ts:81`) | field-level | HTTP | app forms lose the reference picker's constraint |
| MCP server (`server.py`, `client.py`) | both (tool schemas + passthrough) | HTTP | tool schema only |
| WIP Console | both | HTTP | display |
| `wip-toolkit inspect` / `ArchiveModel` | both | archive | reports endpoints as external |
| backup engine | both | **Mongo directly** | archive carries whatever is stored |
| reporting-sync | **neither** (verified: no matches) | — | none |
| template-store itself | both | Mongo | shape validation, diff, widening |

`_to_template_response` passes both lists through untouched — there is no
rendering layer today. Whatever is stored is what every consumer sees.

---

## 6. Form handling and the resolution layer

Three forms are accepted wherever a template reference appears: canonical
UUID, bare value (resolves in the caller's own namespace), and `ns:VALUE`.

Resolution sites relevant here:

- `_normalize_field_references` (`:3097`) — resolves **field-level** refs to
  canonical ids. Called at W1, W2, W3. Never touches the template-level lists.
- `_ref_lists_equivalent` (`:988`) — canonical-entity comparison. **Four call
  sites**: the mirror-lock check (`:114`), the field-property comparison
  (`:1039`), and the upsert immutability diff (`:1073`, `:1083`).
- `_widen`'s `_canonical` / `_value_form` (`:1682-1699`) — resolves both sides
  before dedup, then converts back to value form, with a Mongo lookup per
  endpoint.

**Auto-synonyms are why value form works at all.** `create_template` registers
a Registry auto-synonym `{ns, type: template, value}` → `template_id` for
version 1 *and for every version during restore* (`:584-598`, with the comment
naming this case explicitly: "edge types resolve value-form
source/target_templates there"). A value therefore resolves to whatever
canonical id that value has **in the target namespace**, which is precisely why
a value-form declaration survives a fresh restore untouched.

**Restore's id map.** `remap_restore.py` builds `id_map["templates"]`
(old → new) and `IDRemapper` rewrites the enumerated references. There is no
generic id walk: `remap_template` (`remap.py:57-84`) rewrites `extends` and
field-level refs only, by name.

**Three shipped defects already caused by this ambiguity**, each recorded in a
code comment at the site that fixed it:

- **CASE-406** — JSON-byte equality flagged value↔UUID asymmetry as
  `modified_existing`, spuriously versioning templates. Fixed by making
  comparisons canonical-entity based (`:1030`).
- **CASE-515** — a resolved-canonical addition compared against raw stored
  strings never matched, so an already-allowed endpoint was appended a second
  time in the other form ("the value↔UUID duplicate APP-KB hit", `:1671-1680`).
- **CASE-525** — the `latest` matching branch called `get_template` namespace-
  free and matched by `.value`, which "404s on a namespace-scoped value-form
  endpoint → empty family → **every edge rejected**" (`validation_service.py:
  1705-1711`). Production edge writes failed outright. Fixed by resolving every
  allowed entry through the Registry before matching, explicitly so the stored
  form no longer matters at that site.

CASE-830 is the fourth. The pattern is stable and now four-for-four: every site
that compares or rewrites these lists must independently remember that the
stored form is arbitrary, and each site that forgets produces a distinct bug.
That is the argument for removing the ambiguity rather than adding a fifth site
that remembers.

---

## 7. Defects and hazards in the current state

| | Defect | Status |
|---|---|---|
| H1 | Fresh restore leaves an id-form declaration pointing at the source install | CASE-830, reproduced |
| H2 | `_widen` writes value form into the field-level list, which the **pinned** matching branch compares by exact id (`:1693`) with no resolution | narrow — the `latest` branch (the default) resolves every entry through the Registry first, precisely because of this (CASE-525). Retired for `latest`; survives only for `version_strategy: pinned` |
| H3 | An empty field-level list means **no constraint** (`:1687`), so any reader that loses the projection fails permissive, not loudly | structural |
| H4 | `_value_form` costs one Mongo query per endpoint on every widen | performance, minor |
| H5 | The enforcement the design specified (template-level, via Registry) does not exist; enforcement is an accident of the generic validator | §3 |

---

## 8. Clean design

Constraints given (Peter, 2026-07-31): clean design, no compromises for
compatibility or to rescue existing backups; keep backup/restore fast (direct
Mongo, bulk); where speed forces a trade, resolve clearly by name using
auto-synonyms; canonical IDs are backfilled during or after restore as part of
the process.

### Principles

1. **One declaration.** The template-level `source_templates` /
   `target_templates` are the single source of truth. Nothing else stores the
   same fact.
2. **Canonical in storage.** Stored as canonical template ids — Vision §3, the
   Registry is the identity authority. `:562`'s as-submitted assignment is the
   principle violation that admits every form ambiguity in §6.
3. **Form is an edge concern.** Values resolve **in** at ingress (all of W1–W4)
   and render **out** at egress. Egress has exactly one choke point:
   `_to_template_response`.
4. **Fast paths stay fast.** Backup and restore keep reading and writing Mongo
   directly in bulk. Canonical-id backfill is a bulk step inside the existing
   remap, not a per-row service call.

### Two shapes for the enforcement projection

**D1 — derive the projection at the serve boundary.** The field-level
`target_templates` on `source_ref`/`target_ref` stops being stored and is
computed in `_to_template_response` from the declaration. Every HTTP consumer
(document-store's validator, `@wip/client`, MCP, Console) is unaffected because
they all read that response. The mirror-lock check (`:114`) is deleted — there
is nothing left to compare. Archives carry no projection, which is fine because
nothing reads Mongo except the backup engine.

**D2 — enforce directly from the declaration, no projection at all.**
Document-store's relationship validation (`document_service.py:270`, which
already exists and already knows it is handling an edge type) checks the
resolved endpoint's template against the declaration, resolving through
Registry. This is what `document-relationships.md:93-94` specified and §3 shows
was never built. The second representation disappears entirely rather than
being derived.

**Decision: D1 and D2 ship together.** The concern about combining them was
that D2 must re-implement what the generic validator gives for free —
`include_subtypes` expansion, `version_strategy`, pinned-version handling. The
`include_subtypes` decision below removes the largest of those, and the
remainder is small: the `latest` branch's Registry-resolve-then-match
(`validation_service.py:1712-1735`) is the whole algorithm, and it already
exists to be moved rather than invented.

Shipping them together also avoids an intermediate state in which the
projection is derived but enforcement still reads it — a shape that would be
correct but would leave the subsystem with two representations for one more
release, which is what this work exists to end.

### What changes, precisely

| Change | Where | Note |
|---|---|---|
| C1 | Resolve template-level lists to canonical at ingress | `template_service.py` W1, W2, W3, W4 | reuse the resolver `_normalize_field_references` already builds |
| C2 | Stop `_value_form` downgrading; store canonical | `:1691-1699` | also removes H4's per-endpoint query |
| C3 | Render value form on egress | `_to_template_response:3043` | one choke point; keeps API/Console/`inspect` readable, preserves the documented contract at `api_models.py:114` |
| C4 | Derive the field-level projection on egress; stop storing it | `_to_template_response` | D1 |
| C5 | Delete the mirror-lock comparison at `:114` | keep the rest of the shape validation (fields exist, `reference_type: document`) | **keep `_ref_lists_equivalent`** — three other call sites |
| C6 | Remap the template-level lists | `remap.py:remap_template` | the canonical-id backfill; bulk, in-process, no service calls |

### `include_subtypes` does not apply to edge-type endpoints

**Decision: A.** An edge type's declared endpoints are exactly the templates
listed. Subtypes are not admitted implicitly, and there is no template-level
flag to admit them. Plain reference fields keep `include_subtypes` unchanged —
this narrows the rule for edge endpoints only.

Reasoning, in order of weight:

1. **The declaration stays literal.** What `inspect`, the Console and the API
   report as an edge type's allowed endpoints *is* what gets enforced. With
   expansion, the declaration becomes a seed and the real allowed set is
   computed elsewhere — reintroducing declaration/enforcement drift into the
   one subsystem where that has now caused four defects.
2. **Edge types are an app-visible contract.** `/relationships` and `/traverse`
   are features apps build on, so silently admitting subtypes changes
   application behaviour without changing the declaration.
3. **Performance, on the hottest write path.** Under `version_strategy: latest`
   (the default), `_resolve_document_reference` resolves *every* entry in the
   allowed list through the Registry, per reference, per document. Expansion
   multiplies that list by the subtree size, so it multiplies per-reference
   Registry lookups during bulk edge ingest — and edges are the
   high-cardinality entity in any graph-shaped app. Expansion itself costs
   `get_template` + `get_template_descendants` per distinct endpoint per batch,
   the latter an uncached BFS with one Mongo query per node
   (`inheritance_service.py:304`). (The Registry client's own caching softens
   the per-reference cost; not measured.)
4. **Nothing uses it.** Measured across all 28 archives: 95 distinct templates,
   39 edge types, **zero** using `extends`, **zero** setting
   `include_subtypes`.
5. **Under D1/D2 the flag has no home.** The field-level projection is derived,
   so retaining the capability would mean inventing template-level declaration
   surface for a feature with no users.

Cost accepted: a taxonomy owner must list subtypes explicitly and widen the
edge type when adding one. `add_edge_type_endpoints` already supports exactly
that, additively and in place. If a real taxonomy-plus-edge-type case appears,
a template-level flag remains available — designed then against a concrete
usage pattern rather than a hypothetical.

### Archive format version

**Decision: bump it.** Archives written after this change carry canonical
declarations and no field-level projection. The bump makes that detectable by a
reader instead of silent, which matters because the failure mode of an
un-bumped older reader is permissive (H3), not loud.

**Why C6 is not optional.** Today value form accidentally survives a restore,
which is the only reason CASE-830 is `fyi`. Under canonical storage nothing
survives by accident: ship C1 without C6 and *every* fresh-restored edge type
loses its declaration. C1 and C6 must land together.

**Where auto-synonyms carry the load.** For any endpoint the id map cannot
cover — an archive referencing a template outside it — resolution falls back to
the value through the Registry auto-synonym, which resolves in the *target*
namespace by construction. That is the "be smart, resolve by name" path, and it
is why canonical storage is safe rather than brittle.

### Accepted consequences

- Archives written after this change carry canonical declarations and no
  projection. Restoring one into a **pre-change** install would leave endpoint
  enforcement unconstrained (H3). Accepted by direction: clean slate, no
  compatibility compromise — mitigated by the format-version bump above, which
  makes the mismatch detectable rather than silent.
- Existing stored rows hold value form. No backfill is required for
  correctness — ingress resolution and the canonical-entity diff make old and
  new forms compare equal, so bootstrap re-runs do not spuriously version
  (this is what `_ref_lists_equivalent` at `:1073` already guarantees). A
  one-time normalisation is optional tidiness, not a prerequisite.

---

## 9. Test obligations

- Ingress: create/update/activate/widen with value, id and `ns:VALUE` → stored
  canonical in every case.
- Egress: the served template renders value form and carries the derived
  projection; asserted through the same path document-store uses.
- Restore: fresh restore of an edge type declared in **either** form leaves a
  declaration naming a template that exists in the target namespace. This is
  CASE-830's reproduction, generalised.
- H3 enforcement (the test CASE-830#4 asked for): a stored row read through a
  path that does **not** derive must not silently produce an unconstrained
  reference field — assert refusal or an explicit signal, so the decision is
  enforced rather than merely written down.
- `include_subtypes`: a document whose template *extends* a declared endpoint
  is **rejected** as an edge endpoint. This is the decision above made
  enforceable — without it, "subtypes are not admitted" is an intention.
- H2 (narrowed): an edge-type endpoint field with `version_strategy: pinned`,
  after a widen. Worth one test to pin the behaviour, but no longer the
  general hazard the first draft claimed.

---

## 10. Decisions and remaining questions

All three questions this analysis opened were settled by Peter on 2026-07-31:

| Question | Decision |
|---|---|
| D2's timing — follow-on, or together with D1? | **Together** (§8) |
| Bump the archive format version? | **Yes** (§8) |
| Does `include_subtypes` apply to edge-type endpoints? | **No** — option A (§8) |

Remaining, and deliberately not answered here:

1. Whether the id-preserving restore path (W6) needs anything at all. It
   preserves ids by design, so canonical declarations stay valid; recorded so
   the next reader does not have to re-derive that it was considered.
2. Whether `_value_form`'s removal (C2) leaves any caller depending on
   value-form storage rather than value-form *rendering*. The reader map (§5)
   says no, since every HTTP consumer goes through `_to_template_response` —
   but that is an argument, not a test, until the egress tests in §9 run.
