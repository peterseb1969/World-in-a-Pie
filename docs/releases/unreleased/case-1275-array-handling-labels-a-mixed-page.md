---
section: fixes
cases: [1275]
packages: [wip-client]
---

### `array_handling` on a table-view page now holds for every row it labels

The table view reports one `array_handling` value for the page. It took the last
document's handling, so a page whose first document overflowed
`max_cross_product` and whose last was expanded reported `flattened` — a label
under which no array cell is JSON — while its own rows carried JSON cells. The
value is now the one that holds for the whole page: `json` when any document on
the page kept arrays as JSON cells (other documents may still be expanded),
`flattened` when every array on the page became rows, `none` when the page has no
arrays. `columns[].is_flattened` follows the same rule as before.

**Action:** apps that read array cells should parse them as JSON whenever
`array_handling` is `json`, which can now describe a mixed page; nothing changes
for a page that is uniformly expanded.
