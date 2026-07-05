#!/usr/bin/env bash
# SessionStart[matcher=compact] re-anchor (CASE-480). Plain stdout on exit 0 is
# injected into context before the next turn. Do not auto-run slash commands here
# (unsupported) — nudge; running /wip-wake is the model's job.
cat <<'REANCHOR'
[post-compaction re-anchor] Context was just compacted — your baseline reading
(Vision.md, wip://ponifs, wip://data-model, wip://conventions, the deployable-app
contract) was likely evicted. This is NOT a seamless continuation; that feeling is
exactly when drift starts.

  → RUN /wip-wake NOW, before continuing. It rolls this session and reloads the
    baseline. Do not judge whether you "still remember" — you cannot reliably tell
    what compaction dropped.

Core invariant that must survive every compaction:
  WIP's primitives are your only data model — namespaces, terminologies, terms,
  templates, documents, files, relationships. metadata.* is a throwaway scratchpad,
  never identity, config, or schema. If your code reads metadata back as structure,
  you have built a sidecar model — stop and model it properly. A data-model change
  is a design event: get approval, do not inline it, do not route around it via
  metadata.
REANCHOR
