---
section: fixes
cases: [1275]
---

### A terminology a field cannot resolve says where it looked

A field's `terminology_ref` given as a bare value resolves in the document's
own namespace only. When nothing was found there, the refusal said only
`Terminology 'COUNTRY' not found`, which reads as a missing vocabulary when the
name may simply be under-specified. It now names the namespace it searched and
how to reach another namespace's copy (`'ns:VALUE'`, or the terminology's id) —
the distinction the platform's own resolve layer already draws. A qualified name
or a canonical id gets no such hint, because neither is under-specified.
