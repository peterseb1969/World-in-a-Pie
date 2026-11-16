---
section: fixes
cases: [1343]
---

### Merge: a term the archive retired adds no names to the target's term

Under `extend_terminologies`, a merge gave the target's copy of a term the
names only the archive's copy had, even when the archive's term was
deprecated or inactive: the target's live term gained a name the source no
longer resolves. Such a term now adds none; the target's names stand and the
difference is reported in `target_wins`, as for a target term that is not
active. The `unique_names` refusal also names the terminology the terms land
in (the target's, an externally mapped one, or one the merge adds) instead of
always calling it the target's.
