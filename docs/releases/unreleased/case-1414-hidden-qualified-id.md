---
section: fixes
cases: [1414]
---

### A hidden template or terminology named by `ns:VALUE` answers exactly as an unknown one

A key with several namespaces derives no namespace, so a qualified id (`ns:VALUE`) is resolved by its own prefix. When that id named a template or terminology in a namespace the key cannot read, five doors answered its not-found with the entity's canonical id: template-store `POST /templates/{id}/validate`, `GET /templates/{id}/dependencies` and `POST /templates/{id}/cascade`, and def-store `GET /terminologies/{id}/dependencies` and `POST /terminologies/{id}/terms`. An id that names nothing was answered with the id as submitted. The difference confirmed that the hidden entity exists and gave its canonical id away. All five now answer with the id as submitted, so the two answers are identical.
