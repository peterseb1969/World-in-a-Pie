---
section: action-required
cases: [1286, 1120]
packages: [wip-client]
---

### One open deletion per namespace, enforced by the store; new refusals on DELETE and resume-delete

- **At most one deletion journal per namespace can be open** (`in_progress`
  or `failed`). A partial unique index on `namespace_deletions` enforces it,
  built when the Registry starts. Before, a `resume-delete` that landed while
  a deletion was still building its journal (or two resumes at once) could
  store a second journal and run a second deletion beside the first.
  - **Action:** if an install already holds two open journals for one
    namespace, the index is not built. The Registry starts anyway and logs
    the namespaces at CRITICAL ("Index one_open_journal_per_namespace_idx not
    built"). Finish one deletion with `resume-delete`, resolve the other, and
    restart.
- **`resume-delete` needs `force=true`** (400 without it) when it builds a
  journal for a namespace locked with none and the namespace has
  high-severity inbound references, as `DELETE` does.
  `@wip/client`'s `resumeDeletion(prefix, { force })` passes it.
- **`resume-delete` answers 409** while another run holds the deletion,
  including one still building its journal. It answers 404 when the deletion
  finished between its read and its claim.
- **`DELETE /namespaces/{prefix}` answers 409** when another run holds the
  namespace's deletion, or took over this run's (it was a 500). It answers
  409 naming `resume-delete` when an earlier deletion of the name stopped on
  a failed step and is not finished; the namespace is then given back
  unlocked, and nothing is deleted.
- **A running deletion keeps its claim** for as long as it runs: a single
  long step (one large delete, many file batches) is no longer taken over by
  another run after 15 minutes.
