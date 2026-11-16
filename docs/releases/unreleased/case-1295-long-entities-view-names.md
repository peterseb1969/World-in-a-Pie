---
section: fixes
cases: [1295]
---

### Reporting: a long template value keeps its identity-core view, under the name PostgreSQL stores

- A template value of 50–59 bytes, or a `reporting.table_name` override of 54–63 bytes, makes `<base>__entities` longer than PostgreSQL's 63-byte limit. PostgreSQL then creates the view under a truncated name.
- Reporting-sync now uses that stored name everywhere: `GET /table-name` reports it, `/tables` lists the view under its entity, and the sync finds the existing view. Before, the sync missed the view because it looked for the long name, and so rebuilt it on every event.
- `GET /table-name` for such a value answered 500; it now answers 200.
- **Correction to an earlier note.** The pre-fragments note says a long `__entities` name is refused. It is not: it is named as stored, as above. Its truncation example, "v1/v2 of a 59-byte base", is also wrong: `__v1` and `__v2` on a 59-byte base are exactly 63 bytes and distinct. Two version tables collide on a 60-byte base (v1 and v2), or on a 59-byte base for v1 and v10. Version-table names over 63 bytes are still refused.
