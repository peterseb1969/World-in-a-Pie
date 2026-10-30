<!-- card-sources: what this card condenses; checked by components/mcp-server/tests/test_card_drift.py
docs/Vision.md sha256:d48669c11fbfe408 reviewed:a66e110f
wip://ponifs sha256:ce7da4c122478465 reviewed:c4423374
wip://development-guide sha256:d44cd305a3b84d3e reviewed:a66e110f
scaffold/templates/claude-md/app.md sha256:0bb5acc49a2e3f43 reviewed:a7c74e27
docs/slash-commands/app-builder/wip-design-model.md sha256:88c3300b894dc3ca reviewed:809f9059
-->
# WIP core card — reread on every wake

## What you are building
An app ON TOP of WIP. WIP is a generic engine: it supports nothing out of the box,
and your app maps a domain onto its primitives. Never modify WIP — consume its APIs.
If WIP lacks something, that is a case for the platform, not a workaround in your app.

## The model rule (the costliest drift)
WIP's primitives are your only data model: namespaces, terminologies, terms,
templates, documents, files, relationships (edge types). `metadata.*` is a scratchpad —
never identity, config, sort key or schema. If your code reads metadata back as
structure, you built a sidecar model: stop. Config is a document of a config template;
a controlled vocabulary is terms. A new field, template, terminology or identity change
is a DESIGN event: propose it, get approval, then implement.

## Gates — explicit approval only
The data model needs the user's explicit approval before you implement it, and so does
every later change to it. "Approved", "go ahead", "looks good" or a direct instruction
count. A question, a concern, "let me think about it", silence or your own readiness
does not — wait.

## Ground truth
Git, the filesystem and WIP's answers are the truth; your memory and the transcript are
not. Verify before asserting. Never write a value you did not just read from a command:
timestamps come from `date`, your session ID from `.claude/.session-id`, counts and IDs
from WIP.

## Namespaces
Develop in your dev namespace (named in CLAUDE.md under "Dev Namespace"). MCP calls run
with an admin key that spans namespaces: always pass `namespace=` explicitly.

## The nine PoNIFs (WIP behaves unconventionally here — on purpose)
1. Nothing ever dies: inactive = retired, still resolves for existing references.
2. Re-creating a template makes a new version; the old one stays active — deactivate it.
3. Identity fields decide create-vs-update. No timestamps or per-run data in them;
   none at all = append-only, and such documents cannot be patched.
4. Bulk writes return 200 even when items fail — check every item's status.
5. Several IDs for one entity (synonyms) is normal, not corruption.
6. "Latest active" template lookups are cached ~5 s — pass template_version explicitly.
7. Edge types are templates with usage: relationship — different rules; check `usage`.
8. A template with versioned: false (typically an edge type) overwrites in place — no history.
9. Another namespace is closed by default: referencing it needs read on it, and a refusal
   reads as "not found". References a document already holds carry over.

## WIP's principles, in one line each
Prefer deactivation over deletion · references must resolve, and any synonym works like
the canonical ID · the Registry is the identity authority (no app-side identity by hash
lookup — use the document_id the API returns) · preserve original values ·
configuration over code.

## When the card is not enough
Data model detail → docs/cards/model.md, then wip://data-model and wip://conventions.
Building the app → docs/cards/build.md. Deploying → docs/cards/ship.md.
Why WIP is this way → docs/Vision.md. A PoNIF in depth → docs/WIP_PoNIFs.md.
