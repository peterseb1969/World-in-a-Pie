# Edge-Type Endpoint Declaration — Analysis and Design

**Status:** analysis (§1–§7) current-state, mapped end to end; design (§8) decided
**Date:** analysis 2026-07-31; design decided 2026-08-04
**Tracking:** CASE-830 (defect), CASE-827 (found while building the M1 archive model)
**Model:** FIRESIDE-29 — an ID is an anchor for an identity; synonyms are co-equal anchors
**Related shipped defects in this subsystem:** CASE-406, CASE-515, CASE-525
**Decisions (Peter):** two-half endpoint entries; API serves the entries as the
one shape; restore warns (not refuses) on unresolvable out-of-archive endpoints;
`include_subtypes` does not apply to edge-type endpoints; D1 and D2 ship
together; archive format version bumped (minor)
**Implementation status:** branch `feat/edge-endpoint-declaration` carries a
superseded single-slot implementation of an earlier §8; it is unmerged and must
be reworked to this design before merge.

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
> over HTTP, and every template-serving endpoint returns `TemplateResponse` —
> but NOT all through one construction site. `_to_template_response`
> (`template_service.py:3043`) has 7 call sites, and two routes
> (`add_edge_type_endpoints`, `reactivate_template`) return raw `Template`
> documents that FastAPI's implicit `response_model` coercion converts,
> bypassing the serializer. Any serve-time derivation must route those two
> through the converter and audit all 13 template-returning routes.

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

## 8. The design: two-half endpoint entries

Constraints given (Peter): the FIRESIDE-29 identity model governs — an ID is an
anchor for an identity, synonyms are co-equal anchors, and a fresh restore
re-anchors (identities and synonyms survive; canonical IDs are swapped through
the re-registration mapping table; for references to entities **not in the
archive** no mapping-table entry can exist, so the synonym is the only
resolution mechanism). Keep backup/restore fast (direct Mongo, bulk).

### Why two halves

The endpoint declaration is a set of references, and a reference has two
halves: **which identity** it names (the submitted anchor — a synonym or value
form, the half that survives re-anchoring) and **what it resolved to here**
(the canonical anchor — exact, comparable, remappable through the mapping
table). Documents already store both: `references[]` carries
`{lookup_value, resolved{…}}`, the remapper rewrites only the resolved half,
and Vision §4 ("Preserve Original Values") is the standing commitment behind
that shape.

The template-level endpoint lists were the one reference surface in WIP that
stored a single half, and either single-slot choice loses the other: keep the
submitted form and every consumer must guess what the string is (the §6
four-defect lineage); keep the canonical id and out-of-archive references
become unrecoverable after a fresh restore. The two-half entry removes the
guess structurally — each half has exactly one meaning.

### Principles

1. **One declaration.** The template-level `source_templates` /
   `target_templates` are the single source of truth. Nothing else stores the
   same fact.
2. **Both halves, fixed semantics.** Each entry is
   `{lookup_value, resolved}`: `lookup_value` is the caller's anchor kept
   verbatim; `resolved` is the canonical template id, filled by the platform.
   No consumer ever infers meaning from a string's shape again.
3. **`resolved` is server-owned.** It is computed at ingress via the Registry
   (`bypass_cache=True` — writes hit Registry) and recomputed from
   `lookup_value` on every write; a caller-supplied `resolved` is ignored.
4. **Fast paths stay fast.** Backup and restore keep reading and writing Mongo
   directly in bulk. The remap of resolved halves is a bulk step inside the
   existing remap; the restore-door repair touches only endpoint entries whose
   resolved half the mapping table did not cover (endpoint lists are small).

### Storage shape

```jsonc
"source_templates": [
  {"lookup_value": "MONSTER",    "resolved": "019fb7a6-3d44-7dd7-…"},
  {"lookup_value": "kb:SESSION", "resolved": "019f4c11-…"}
]
// resolved: canonical template_id, or null only when a restore could not
// re-resolve an out-of-archive endpoint on the target (job warning attached)
```

- `lookup_value` — bare value (own namespace), `ns:VALUE`, or UUID, exactly as
  submitted. Never rewritten except the namespace half of a qualified form
  when that namespace is re-minted (the same rule
  `_remap_document_reference` applies to document lookups).
- `resolved` — canonical id, or `null` after a restore that could not repair
  it (see below). A `null` resolved half fails **closed** wherever an exact id
  is required (e.g. `version_strategy: pinned`).

### Ingress (W1–W4)

All four write paths accept plain strings (any form) or full entries; either
way `lookup_value` keeps the submitted anchor and `resolved` is computed
server-side. `_widen` dedups by resolved half; its resolve-then-downgrade
(`_value_form`) disappears, which also removes H4's per-endpoint Mongo query.
The create-as-upsert immutability diff compares **resolved sets**, so
synonym-equivalent declarations compare equal without the Registry round-trips
`_ref_lists_equivalent` needs today (the CASE-406 spurious-versioning class
stays closed by construction).

### Serve — the entries are the one shape (D1 kept)

`TemplateResponse.source_templates` / `target_templates` serve the entries as
stored — both halves, one shape, no string-list projection beside them. A
convenience copy at the API layer would recreate the declaration/projection
drift pattern one level up: every reader would again have to know which field
is authoritative.

The **field-level** `target_templates` on `source_ref` / `target_ref` remains
what it always should have been: a projection for the generic reference
validator, **derived at serve** in `_to_template_response` and never stored.
It is fed the resolved halves (falling back to `lookup_value` where resolved
is null); `include_subtypes` is forced off. The mirror-lock comparison at
`:114` is deleted — there is nothing left to compare. Enforcement semantics
are unchanged: the `latest` branch resolves every entry through the Registry
before matching (CASE-525), `pinned` compares canonical ids.

### What changes, precisely

| Change | Where | Note |
|---|---|---|
| C1 | Endpoint entries become `{lookup_value, resolved}`; ingress fills `resolved`, keeps `lookup_value` verbatim | `template_service.py` W1, W2, W3, W4 + `models/template.py` / `api_models.py` | plain-string submissions remain valid; `resolved` is server-owned |
| C2 | `_widen` writes entries and dedups by resolved half | `:1682-1699` | `_value_form` deleted (H4 gone) |
| C3 | Serve the entries as the one shape | `_to_template_response` + every `TemplateResponse` consumer | breaking read-shape change, one delivery train |
| C4 | Derive the field-level projection on egress; stop storing it | `_to_template_response` | fed resolved-else-lookup; subtypes forced off |
| C5 | Delete the mirror-lock comparison at `:114`; upsert diff compares resolved sets | keep the rest of the shape validation | `_ref_lists_equivalent`'s remaining call sites reviewed against the new diff |
| C6 | Remap resolved halves; rewrite qualified lookup namespaces | `remap.py:remap_template` | same rules as `_remap_document_reference`; bulk, in-process |
| C7 | Restore-door repair for out-of-archive endpoints | restore engine, post-remap | see below; **warning, never refusal** (decided) |
| C8 | Permissive-empty fails loud | document-store validator | an edge type's ref field with no constraint is an error, not a pass |

### Fresh restore: remap, then repair (C6 + C7)

`remap_template` rewrites each entry's `resolved` through the template map
(pass-through when absent) and rewrites the namespace half of a qualified
`lookup_value` when that namespace is re-minted — the document-reference rules
applied to the declaration. C1 and C6 land together: entries whose resolved
halves are not rewritten would name the source install.

Then the door applies the FIRESIDE-29 mechanism to every entry the mapping
table did not cover (an out-of-archive endpoint): re-resolve `lookup_value`
against the **target's** Registry.

- Found → `resolved` is repaired to the target's canonical id.
- Not found → `resolved: null` plus a **job warning** (decided: warning, not
  refusal — consistent with how restore treats dangling document references).
  Nulling is required, not cosmetic: a stale source-install id persisted into
  a fresh namespace is exactly what the matrix's PL-LEAK sweep forbids.

The id-preserving restore path (W6) needs nothing: ids are preserved by
design, so both halves stay valid.

### Permissive-empty fails loud (C8)

`validation_service.py:1687` treats an empty constraint as *no constraint*.
For plain reference fields that is a feature; for an edge type it means any
reader that loses the derived projection silently drops endpoint enforcement
(H3). Decision: document-store — which knows `usage` (it already runs the
edge-specific namespace/archived checks) — **refuses** an edge-type
`source_ref`/`target_ref` that reaches the reference validator with no
constraint. The obligation is enforced by a test that reads a stored row
through a path that does **not** derive and asserts refusal (CASE-830#4's
requirement), so the decision is enforced rather than merely written down.

### D2 — enforcement location

D2 ("enforce directly from the declaration in document-store, no projection at
all") is delivered by C4 + C8 + the subtype rule: enforcement flows from the
declaration, cannot diverge from it, and its absence is loud. The projection
survives as **served** schema information — `@wip/client`'s `template-to-form`
builds reference pickers from it. Moving the check's code into document-store
would be a location preference, not a behavioural change; not done.

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

**Decision: bump it (minor).** Archives written after this change carry
two-half endpoint entries and no field-level projection. The bump makes that
detectable by a reader instead of silent, which matters because the failure
mode of an un-bumped older reader is permissive (H3), not loud. The bump stays
**minor** (3.x): every existing gate tests `format_version.startswith("3")`
(`api/backup.py:199`, `backup_engine.py:2408`, `convert_archive.py:37`), so a
major bump would make every new archive unrestorable on an install that has
not taken this change — a self-inflicted break far larger than the defect.

**Legacy rows and archives read tolerantly.** A bare string entry in a stored
row or an older archive is read as `{lookup_value: <string>, resolved: null}`;
the resolved half fills at the next write, restore-door repair, or an optional
one-time normalisation. Measured population (28 archives, 365 edge types,
2,662 endpoint entries, all value-form, zero id-form) makes this a
non-event operationally — but tolerant reading is the design, not a bet on
that measurement.

### Accepted consequences

- **The read shape is a breaking change** for every `TemplateResponse`
  consumer: `@wip/client` (types + minor bump), MCP schemas, the Console's
  edge-type views (APP-RC), `ArchiveModel`/`inspect`, plus a sweep check on
  reporting-sync's templates definitions table. All in-house; updated in the
  same delivery per the libs-ship-with-features rule.
- Restoring a post-change archive into a **pre-change** install leaves endpoint
  enforcement unconstrained there (H3 on the old reader) — detectable via the
  format bump, accepted by direction.
- An entry whose `resolved` is null cannot satisfy `version_strategy: pinned`
  (fails closed) until re-resolved; `latest` (the default) resolves the
  `lookup_value` through the Registry as it always has.
- Raw API and Console show entry objects rather than bare strings; clients
  render `lookup_value` for humans.

---

## 9. Test obligations

- Ingress: create/update/activate/widen with bare value, `ns:VALUE` and UUID →
  `lookup_value` kept verbatim, `resolved` canonical, in every case; a
  caller-supplied `resolved` is ignored (server-owned half).
- Upsert diff: value-form and UUID-form declarations of the same endpoints
  compare **unchanged** (resolved-set comparison; the CASE-406 class).
- Egress: the served template carries the entries and the derived field-level
  projection (resolved-else-lookup, subtypes off); asserted through the same
  path document-store uses.
- Restore, in-archive: fresh restore of an edge type declared in **either**
  form leaves `resolved` naming the target namespace's template and
  `lookup_value` intact (bare stays; qualified namespace half rewritten).
  CASE-830's reproduction, generalised.
- Restore, out-of-archive, target has the identity: the door repairs
  `resolved` to the target's canonical id via the `lookup_value` synonym.
- Restore, out-of-archive, target lacks the identity: `resolved: null` + job
  warning, and **no source-install id survives anywhere in the row**
  (the PL-LEAK invariant).
- Legacy read: a bare-string entry is read as `{lookup_value, resolved: null}`
  and heals on the next write.
- H3 enforcement (the test CASE-830#4 asked for): a stored row read through a
  path that does **not** derive must be refused, not silently unconstrained —
  so C8 is enforced rather than merely written down.
- `include_subtypes`: a document whose template *extends* a declared endpoint
  is **rejected** as an edge endpoint. This is the decision above made
  enforceable — without it, "subtypes are not admitted" is an intention.
- `version_strategy: pinned`: matches on the resolved half after a widen;
  fails closed on a null resolved half. (H2, narrowed — the `latest` branch
  resolves through the Registry and is form-agnostic.)

---

## 10. Decisions and remaining questions

| Question | Decision |
|---|---|
| Storage shape for the declaration | **Two-half entries** `{lookup_value, resolved}` (Peter, 2026-08-04) |
| API read shape | **The entries are the one shape** — no string-list projection beside them (Peter, 2026-08-04) |
| Restore posture for unresolvable out-of-archive endpoints | **Warning + `resolved: null`**, never refusal (Peter, 2026-08-04) |
| Permissive-empty (H3) | Edge-type ref field with no constraint is **refused** by the validator, pinned by a non-deriving-path test (§8 C8) |
| Does `include_subtypes` apply to edge-type endpoints? | **No** — option A (Peter, 2026-07-31) |
| D2's timing — follow-on, or together with D1? | **Together**; delivered by C4 + C8 + the subtype rule (Peter, 2026-07-31) |
| Bump the archive format version? | **Yes, minor** (Peter, 2026-07-31; rationale in §8) |

Remaining, and deliberately not answered here:

1. Whether the id-preserving restore path (W6) needs anything at all. It
   preserves ids by design, so both halves stay valid; recorded so the next
   reader does not have to re-derive that it was considered.
2. Whether document `references[]` should receive the same **door repair**
   (out-of-archive resolved halves re-resolved on the target via
   `lookup_value`). The endpoint-list repair in §8 C7 applies FIRESIDE-29's
   mechanism to a small list; documents are the high-cardinality case and
   belong to the CASE-827 M2 conversation, not this one.
3. Whether the one-time normalisation of legacy string entries is worth
   running anywhere, given tolerant reads make it optional.
