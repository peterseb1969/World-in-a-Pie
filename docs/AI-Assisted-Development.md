# AI-Assisted Development on WIP

## The Experiment

Can an AI assistant (like Claude) program solutions that leverage WIP as a backend?

This is a central experiment of the World In a Pie project. If WIP's primitives are well-documented and its APIs are consistent, an AI should be able to:

1. **Understand** the WIP data model (terminologies, templates, documents)
2. **Design** appropriate schemas for a given domain
3. **Generate** working code that interacts with WIP APIs
4. **Build** complete applications on top of WIP

---

## Why WIP is AI-Friendly

### 1. Consistent Primitives

WIP has only four core concepts:

| Concept | AI Understanding |
|---------|-----------------|
| **Terminology** | "A controlled vocabulary with codes, values, and aliases" |
| **Template** | "A JSON Schema-like definition with field types and validation rules" |
| **Document** | "Validated data conforming to a template, with versioning" |
| **Reporting** | "SQL-accessible flattened view of documents" |

An AI doesn't need domain expertise—it needs to map any domain onto these primitives.

### 2. RESTful APIs

All interactions are via standard REST endpoints:

```
POST   /api/def-store/terminologies          Create terminology
POST   /api/def-store/terminologies/{id}/terms   Add term
POST   /api/template-store/templates         Create template
POST   /api/document-store/documents         Create document
GET    /api/document-store/table/{id}        Get tabular view
```

An AI can generate HTTP client code for any language.

### 3. Self-Describing Schemas

Templates define their own structure. An AI can:
- Read a template definition
- Generate a matching data entry form
- Validate data before submission
- Handle nested objects and arrays

### 4. Validation Feedback

WIP provides detailed validation errors:

```json
{
  "valid": false,
  "errors": [
    {
      "field": "status",
      "type": "term_not_found",
      "message": "Value 'INVALID' is not a valid term in terminology DOCUMENT_STATUS"
    }
  ]
}
```

An AI can use this feedback to self-correct generated data.

---

## AI Development Workflow

### Step 1: Domain Analysis

User describes their domain:

> "I need to track patient visits. Each visit has a patient ID, date, doctor, diagnosis codes (ICD-10), and notes."

AI identifies required components:
- **Terminologies**: DOCTOR_LIST, ICD10_CODES (or subset)
- **Template**: PATIENT_VISIT with fields for each attribute
- **Validation**: Date must be past or today, diagnosis codes must be valid ICD-10

### Step 2: Schema Design

AI generates terminology and template definitions:

```json
// Terminology: DOCTOR_LIST
{
  "code": "DOCTOR_LIST",
  "name": "Doctors",
  "terms": [
    {"code": "DR001", "value": "Dr. Smith"},
    {"code": "DR002", "value": "Dr. Jones"}
  ]
}

// Template: PATIENT_VISIT
{
  "code": "PATIENT_VISIT",
  "name": "Patient Visit",
  "identity_fields": ["patient_id", "visit_date"],
  "fields": [
    {"name": "patient_id", "type": "string", "required": true},
    {"name": "visit_date", "type": "date", "required": true},
    {"name": "doctor", "type": "term", "terminology_ref": "DOCTOR_LIST", "required": true},
    {"name": "diagnosis_codes", "type": "array", "items": {"type": "string"}},
    {"name": "notes", "type": "string"}
  ]
}
```

### Step 3: API Integration Code

AI generates client code:

```python
import httpx

class PatientVisitClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}

    async def create_visit(self, patient_id: str, visit_date: str,
                           doctor: str, diagnosis_codes: list, notes: str = None):
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/api/document-store/documents",
                headers=self.headers,
                json={
                    "template_code": "PATIENT_VISIT",
                    "data": {
                        "patient_id": patient_id,
                        "visit_date": visit_date,
                        "doctor": doctor,
                        "diagnosis_codes": diagnosis_codes,
                        "notes": notes
                    }
                }
            )
            return response.json()
```

### Step 4: User Interface

AI generates a frontend (React, Vue, or server-rendered):

```vue
<template>
  <form @submit.prevent="submitVisit">
    <input v-model="patientId" placeholder="Patient ID" required />
    <input v-model="visitDate" type="date" required />
    <select v-model="doctor" required>
      <option v-for="doc in doctors" :value="doc.code">{{ doc.value }}</option>
    </select>
    <textarea v-model="notes" placeholder="Notes"></textarea>
    <button type="submit">Record Visit</button>
  </form>
</template>
```

### Step 5: Iteration

User provides feedback:
> "I also need to track vital signs: blood pressure, temperature, heart rate."

AI updates the template:
```json
{
  "name": "vitals",
  "type": "object",
  "properties": [
    {"name": "blood_pressure", "type": "string"},
    {"name": "temperature", "type": "number"},
    {"name": "heart_rate", "type": "integer"}
  ]
}
```

---

## What Makes This Work

### Comprehensive Documentation

The [CLAUDE.md](../CLAUDE.md) file provides:
- Complete API reference
- Data model explanations
- Code examples
- Validation rules
- Common patterns

An AI with access to this documentation can understand WIP deeply.

### Predictable Patterns

Every WIP interaction follows the same pattern:
1. Create terminology (if needed)
2. Create template referencing terminologies
3. Create documents conforming to template
4. Query via table view or direct API

An AI can learn this pattern once and apply it to any domain.

### Error Recovery

WIP's detailed error messages enable AI self-correction:
- "Term not found" → Check terminology, add missing term
- "Field required" → Update form validation
- "Template not found" → Create template first

---

## Example Domains an AI Could Build

| Domain | Terminologies | Templates | Complexity |
|--------|--------------|-----------|------------|
| Personal expense tracker | CATEGORY, PAYMENT_METHOD | EXPENSE | Low |
| Recipe collection | CUISINE, DIFFICULTY, UNIT | RECIPE, INGREDIENT | Low |
| Home sensor logging | SENSOR_TYPE, ROOM, UNIT | SENSOR_READING | Low |
| Bug tracker | PRIORITY, STATUS, COMPONENT | BUG_REPORT | Medium |
| Customer feedback | SENTIMENT, PRODUCT, CHANNEL | FEEDBACK | Medium |
| Equipment maintenance | EQUIPMENT_TYPE, STATUS, TECHNICIAN | MAINTENANCE_LOG, EQUIPMENT | Medium |
| Clinical trials | PHASE, OUTCOME, ADVERSE_EVENT_TYPE | TRIAL, PARTICIPANT, VISIT | High |
| Supply chain tracking | LOCATION, CARRIER, PRODUCT_CATEGORY | SHIPMENT, INVENTORY | High |

---

## Limitations and Considerations

### What AI Can Do Well

- ✅ Design schemas from natural language descriptions
- ✅ Generate API client code in multiple languages
- ✅ Build CRUD interfaces (forms, tables, detail views)
- ✅ Implement basic validation logic
- ✅ Create reporting queries

### What Requires Human Guidance

- ⚠️ Complex business rules (may need iteration)
- ⚠️ Security requirements (authentication scopes, data sensitivity)
- ⚠️ Performance optimization (indexing, query patterns)
- ⚠️ Integration with external systems (APIs, webhooks)
- ⚠️ Edge cases in data validation

### What Remains Human Responsibility

- ❌ Defining business requirements
- ❌ Validating generated code against requirements
- ❌ Testing in production-like environments
- ❌ Compliance and regulatory decisions
- ❌ Ongoing maintenance and evolution

---

## Getting Started with AI Development

### For the AI

1. Read [CLAUDE.md](../CLAUDE.md) thoroughly
2. Understand the four primitives
3. Start with a simple domain (expense tracker)
4. Use WIP's validation feedback to iterate

### For the Human

1. Describe your domain clearly
2. Provide examples of the data you want to store
3. Specify any validation rules or constraints
4. Review generated schemas before implementation
5. Test with real-world data samples

### Prompt Template

```
I want to build a [DOMAIN] application using WIP as the backend.

The main entities are:
- [Entity 1]: [description, fields]
- [Entity 2]: [description, fields]

Relationships:
- [Entity 1] has many [Entity 2]
- [Field X] should be validated against [controlled vocabulary]

Validation rules:
- [Rule 1]
- [Rule 2]

Please design the terminologies and templates, then generate the API client code.
```

---

## The Vision

If this experiment succeeds, WIP becomes more than a data platform—it becomes an **AI-accessible backend** where:

1. Non-programmers describe what they need
2. AI generates the schema and code
3. WIP validates and stores the data
4. PostgreSQL provides SQL access for reporting

The barrier to building data-driven applications drops dramatically. The "pie" becomes accessible to everyone.
