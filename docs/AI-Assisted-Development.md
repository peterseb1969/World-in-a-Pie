# AI-Assisted Development on WIP

## Purpose

This document defines the process for an AI assistant to build applications on top of World In a Pie (WIP). The AI acts as a developer; WIP acts as the backend. The AI interacts with WIP exclusively through MCP tools.

### The Golden Rule

> **Never, ever change WIP. The mission is to leverage it.**

WIP is a generic, domain-agnostic storage and reporting engine. It provides primitives (terminologies, templates, documents, files, reporting). The AI's job is to map a user's domain onto those primitives and build an application layer on top.

---

## The 4-Phase Process

Each phase has a slash command. Follow them in order.

| Phase | Command | Goal | Gate |
|-------|---------|------|------|
| 1. Exploratory | `/explore` | Understand WIP and the user's domain | AI can explain WIP primitives and the user's data |
| 2. Data Model Design | `/design-model` | Map the domain to terminologies, templates, fields | User approves the data model |
| 3. Implementation | `/implement` | Create terminologies, templates, and test documents in WIP | All entities exist, test documents validate |
| 4. Application Layer | `/build-app` | Build the user-facing application | Working app with committed code |

After Phase 4, use `/improve` for iterative enhancements.

**Supporting commands:** `/wip-status` (check WIP health), `/export-model` (save data model to git), `/bootstrap` (recreate data model from seed files), `/document` (generate app documentation), `/resume` (recover context after compaction).

### Phase Gates Are Mandatory

Do not skip phases. Do not proceed without the gate condition being met. The gates exist because:
- **Phase 1 → 2:** Without understanding WIP's primitives, the data model will be wrong
- **Phase 2 → 3:** Without user approval, the AI builds the wrong thing
- **Phase 3 → 4:** Without verified data in WIP, the app has nothing to display

---

## Data Model Design Guide

This is the most consequential part of the process. A wrong data model is expensive to fix after documents exist. Get it right in Phase 2.

### Field Naming — Use WIP's Names

WIP uses specific field names. Using conventional alternatives causes silent failures or confusing errors.

| WIP Name | Common Wrong Name | Where It Appears |
|----------|------------------|-----------------|
| `value` | code, key, id | Terminologies, terms, templates |
| `label` | name, title, display_name | All entities |
| `mandatory` | required | Template fields |
| `terminology_ref` | terminology_id, terminology | Term-type fields |

### Terminologies — Controlled Vocabularies

A terminology is a closed list of allowed values. Use them for any field where free text would cause inconsistency.

**Good candidates:** countries, currencies, statuses, categories, types, priorities
**Bad candidates:** names, descriptions, free-form notes (use `type: "string"`)

Each term has:
- `value` — the canonical value stored in documents (e.g., `"CHF"`)
- `label` — the human-readable display name (e.g., `"Swiss Franc"`)
- `aliases` — alternative inputs that resolve to this term (e.g., `["Franken", "sfr"]`)

### Identity Fields — The Most Important Decision

Templates can declare `identity_fields` — the fields that determine whether two documents represent the same real-world entity. WIP computes a hash from these fields. Same hash = same entity (new version). Different hash = different entity (new document).

**Good identity field choices:**

| Entity | Identity Fields | Why |
|--------|----------------|-----|
| Person | `["email"]` | Email is unique per person |
| Order | `["order_number"]` | Business assigns unique order numbers |
| Invoice Line | `["invoice_number", "line_number"]` | Composite: line is unique within invoice |
| Country | `["iso_code"]` | ISO codes are globally unique |

**Bad identity field choices:**

| Entity | Bad Choice | Problem |
|--------|-----------|---------|
| Person | `["first_name", "last_name"]` | Two "John Smith"s collide |
| Order | `[]` (none) | Every submission creates a new document, no versioning |
| Invoice | `["invoice_number", "amount", "date"]` | Correcting the amount creates a new document |
| Sensor | `["sensor_id"]` | All readings overwrite each other — only latest kept |

**The rule:** Identity fields should include exactly the fields that answer "is this the same real-world thing?" — no more, no less.

**No identity fields = append-only.** This is correct for event logs and audit entries, but means no update path exists. Every submission creates a new document.

### Reference Fields — Linking Documents

Use `type: "reference"` whenever one document points to another. **Never use `type: "string"` for cross-document links** — you lose validation, resolution, and referential integrity.

```json
{
  "name": "customer",
  "label": "Customer",
  "type": "reference",
  "reference_type": "document",
  "target_templates": ["CUSTOMER"],
  "mandatory": true
}
```

This gives you: validated references, automatic resolution by business key or synonym, and referential integrity checks.

**Creation order matters:** Create templates for referenced entities before templates that reference them (e.g., CUSTOMER before INVOICE). Same for documents.

### Term Fields — Linking to Vocabularies

Use `type: "term"` with `terminology_ref` for controlled vocabulary fields:

```json
{
  "name": "currency",
  "label": "Currency",
  "type": "term",
  "terminology_ref": "FIN_CURRENCY",
  "mandatory": true
}
```

WIP validates the value exists in the referenced terminology (including aliases) and stores both the original value and the resolved term ID.

### File Fields — Linking Binary Files

Use `type: "file"` for binary attachments. Files are first-class entities in WIP with their own IDs, reference tracking, and orphan detection. Upload files first, then use their IDs in document data.

### Reporting Configuration

If the user needs SQL queries or dashboards, enable reporting sync on templates:

```json
{
  "reporting": {
    "sync_enabled": true,
    "sync_strategy": "latest_only"
  }
}
```

This syncs documents to PostgreSQL in real-time via NATS events, enabling SQL queries and BI tools.

---

## Reference Resolution Cascade

When a document contains a reference field, WIP resolves it using a 5-step cascade:

```
Input value in reference field
        |
        v
   +-----------------+
   | 1. UUID7 format?|--yes--> Direct document_id lookup
   +--------+--------+
            | no
   +--------v--------+
   | 2. "hash:" ?    |--yes--> Identity hash lookup (active docs)
   +--------+--------+
            | no
   +--------v--------+
   | 3. Registry     |--yes--> Resolved! Fetch doc by canonical ID
   |    lookup       |         If inactive: follow identity_hash
   +--------+--------+         chain to latest active version
            | not found
   +--------v--------+
   | 4. Business key |--yes--> Match against target template's
   |    (string)     |         identity fields
   +--------+--------+
            | not found
   +--------v--------+
   | 5. Composite    |--yes--> Match dict against multiple
   |    key (dict)   |         identity fields
   +--------+--------+
            | not found
            v
      VALIDATION ERROR
   "Referenced document not found"
```

**Practical implication:** You can reference a document by its UUID, identity hash, Registry synonym, single business key, or composite business key. WIP tries all of these in order. This is why identity fields and synonyms matter — they enable flexible reference resolution.

---

## PoNIFs — Quick Reference

WIP has 6 behaviours that violate conventional expectations. Read `wip://ponifs` (MCP resource) for the full version. Here's the summary:

| # | PoNIF | Trap | Rule |
|---|-------|------|------|
| 1 | Nothing Ever Dies | Trying to delete entities | Deactivate, never delete. Inactive = retired, not gone. |
| 2 | Template Versioning | Assuming update replaces old version | Update creates a new version. Old version stays active. Always pass `template_version`. |
| 3 | Document Identity | Adding timestamps or run-specific data to documents | Identity hash determines create vs. update. Extra fields = always-new hash = no versioning. |
| 4 | Bulk-First 200 OK | Checking HTTP status for success | Always 200. Check `results[i].status` for per-item outcomes. |
| 5 | Registry Synonyms | Assuming one ID per entity | Multiple IDs are normal. Merge to reconcile. |
| 6 | Template Cache | Expecting instant effect after template change | Cached for up to 5 seconds. Restart service if urgent. |

**The Compactheimer's Warning:** After context compaction, AI assistants lose PoNIF knowledge and revert to conventional patterns. If you find yourself assuming any of the "Trap" column behaviours feel natural, re-read `wip://ponifs`.

---

## Key Concepts

| Concept | What It Is | Why It Matters |
|---------|-----------|----------------|
| **Terminology** | Controlled vocabulary (closed list of terms) | Enforces data consistency across documents |
| **Term** | A value within a terminology, with label and aliases | Aliases enable fuzzy matching on import |
| **Template** | Document schema defining fields, types, validation | Templates are versioned — multiple versions coexist |
| **Document** | A data record conforming to a template | Versioned via identity hash — same identity = new version |
| **File** | Binary attachment stored in MinIO | First-class entity with reference tracking and orphan detection |
| **Registry** | Central ID authority with synonym support | Enables cross-system integration without mapping tables |
| **Namespace** | Logical partition for all entities | Enables backup/restore, multi-tenancy, data isolation |
| **Reporting Sync** | Real-time MongoDB → PostgreSQL via NATS | Enables SQL queries and BI dashboards over document data |

---

## Common Pitfalls

| Pitfall | Prevention |
|---------|------------|
| No identity fields on template | Every template should have `identity_fields` unless intentionally append-only |
| Using `type: "string"` for cross-document links | Use `type: "reference"` with `target_templates` |
| Too many identity fields | Correcting any field creates a new document instead of a version |
| Too few identity fields | Different real-world entities collide and overwrite each other |
| Adding timestamps to document data | Breaks idempotent import — every submission gets a unique hash |
| Wrong creation order | Terminologies → terms → templates (referenced first) → documents (referenced first) |
| Forgetting reporting config | Add `reporting: {sync_enabled: true}` if SQL/dashboards needed |
| Using `code`/`name` instead of `value`/`label` | WIP uses `value` and `label` everywhere |
| Using `required` instead of `mandatory` | WIP field property is `mandatory`, not `required` |
| Modifying WIP code | Never. Build on top of it, not inside it. |

---

## Summary

1. **Follow the 4 phases** — Explore → Design → Implement → Build
2. **Use slash commands** — each phase has a command with detailed steps
3. **Get identity fields right** — they determine versioning behaviour
4. **Use reference fields** — never strings for cross-document links
5. **Know the PoNIFs** — especially bulk-first 200 OK and template versioning
6. **The user is the domain expert** — the AI is the technical implementer
7. **Never change WIP** — build on top of it
