---
section: fixes
cases: [1275]
---

### The table view of a template with an untyped array field answers again

A template may declare an `array` field without `array_item_type`.
`GET /api/document-store/table/{template_id}` and its `/csv` export answered
500 for such a template; the column is now typed `unknown`.
