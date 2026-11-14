---
section: fixes
cases: [1275]
packages: [wip-archive]
---

### A rule the platform cannot evaluate is refused, never skipped

Document validation skipped a cross-field rule it could not evaluate: a
`range` rule with no bound passed any number, a `mutual_exclusion` naming
fewer than two fields could never fire, and a `conditional_value` without
`allowed_values` or a `dependency` without `conditions` was skipped alike. A
rule element that is not an object ended the request with a 500. Validation now
refuses every shape template-store refuses at write time, per document
(`invalid_rule`, with `rule_index` and `rule_type`), so a template stored
before that check fails its writes instead of pretending the guardrail holds.
A `mutual_exclusion` rule also applies only when its `conditions` hold, like
every other rule type — with none, it applies to every document. `wip verify`
and the toolkit's pre-upload checks (`wip-archive`) refuse the same shapes.

**Action:** documents on a template that carries an unevaluable rule are
refused with `invalid_rule` until the template's rules are fixed.
