# Use Case: Clinical Study Plan

## Overview

A study plan defines the schedule and events for a clinical trial or research study. This use case demonstrates how WIP can model complex study protocols with multiple arms, timepoints, and event types - without native scheduling capabilities.

## Key Challenge

WIP has no built-in notion of time or schedules. We solve this by:

1. **Per-study terminology for timepoints** - Each study gets its own terminology defining valid timepoints (SCREENING, BASELINE, WEEK_4, etc.)
2. **Days from baseline as integers** - Relative time encoded as days/hours from Day 0
3. **Sort order for sequencing** - Terminology terms have sort_order to establish event sequence
4. **Term metadata for schedule info** - Days from baseline stored in term metadata

## Core Concepts

### Day Zero (Baseline)

Every study has a "Day 0" anchor point (usually called Baseline). All other timepoints are relative:
- Screening: Day -14 (14 days before baseline)
- Week 4: Day 28 (28 days after baseline)
- End of Study: Day 84

### Study Arms

Studies often have multiple arms (treatment groups):
- Placebo arm
- Low dose arm
- High dose arm

Different arms may have different schedules or events. A timepoint can apply to:
- All arms (e.g., Baseline)
- Specific arms only (e.g., High Dose Week 8 PK sampling)

### Visit Windows

Clinical visits have acceptable windows:
- "Week 4 visit (Day 28 ±3)" means Day 25-31 is acceptable
- Modeled as `window_days_before` and `window_days_after`

### Events

Multiple events can occur at a single timepoint:
- Visit (physical examination)
- Lab collection (blood draws)
- Questionnaire (patient-reported outcomes)
- Imaging (MRI, CT scan)
- Phone call (safety check)

---

## Data Model

### Terminologies

| Terminology | Scope | Purpose |
|-------------|-------|---------|
| `EVENT_TYPE` | Global | Types of study events |
| `STUDY_PHASE` | Global | Study phases (screening, treatment, etc.) |
| `STUDY_{ID}_ARMS` | Per-study | Study arms/treatment groups |
| `STUDY_{ID}_TIMEPOINTS` | Per-study | Study timepoints with days in metadata |

### Templates

```
STUDY_DEFINITION
  └── study_id (identity)
  └── name, description, phase, sponsor, indication

STUDY_ARM
  └── study_id + arm_code (identity)
  └── name, description, randomization_ratio, is_control

STUDY_TIMEPOINT
  └── study_id + timepoint (identity)
  └── days_from_baseline, window, phase, applicable_arms

STUDY_PLANNED_EVENT (base)
  └── study_id + timepoint + event_type (identity)
  └── applicable_arms, description, is_required
  │
  ├── STUDY_PLANNED_VISIT
  │     └── location_type, duration, fasting_required
  │
  ├── STUDY_PLANNED_LAB
  │     └── sample_types, tube_types, fasting_required
  │
  ├── STUDY_PLANNED_QUESTIONNAIRE
  │     └── form_name, estimated_minutes
  │
  ├── STUDY_PLANNED_IMAGING
  │     └── imaging_type, body_region, contrast
  │
  └── STUDY_PLANNED_PHONE_CALL
        └── call_purpose, estimated_minutes
```

---

## Mapping to WIP Primitives

| Study Concept | WIP Primitive | Notes |
|---------------|---------------|-------|
| Study schedule | Terminology (per-study) | Terms = timepoints, sort_order = sequence |
| Time offset | Term metadata | `{days: 28}` on WEEK_4 term |
| Study arms | Terminology (per-study) | PLACEBO, TREATMENT_HIGH, etc. |
| Event types | Terminology (global) | visit, lab, questionnaire, etc. |
| Timepoint definition | Document (STUDY_TIMEPOINT) | Links to terminology term |
| Planned event | Document (STUDY_PLANNED_*) | What should happen |
| Event sequence | sort_order on terms | Implicit ordering |
| Arm-specific events | applicable_arms field | Which arms include this |

---

## Example: DEMO-001 Study

A Phase 2 clinical trial with the following design:

### Arms
| Arm | Description | Ratio |
|-----|-------------|-------|
| PLACEBO | Placebo control | 1 |
| TREATMENT | Active treatment 100mg | 2 |

### Schedule
| Timepoint | Day | Window | Arms | Events |
|-----------|-----|--------|------|--------|
| SCREENING | -14 | ±3 | All | Visit, Labs, Consent, Questionnaire |
| BASELINE | 0 | 0 | All | Visit, Labs, Questionnaire, Imaging |
| WEEK_2 | 14 | ±2 | All | Phone call (safety) |
| WEEK_4 | 28 | ±3 | All | Visit, Labs |
| WEEK_8 | 56 | ±3 | All | Visit, Labs, Questionnaire, Imaging |
| END_OF_STUDY | 84 | ±7 | All | Visit, Labs, Questionnaire, Imaging |
| EARLY_TERM | - | - | All | Visit, Labs (for early discontinuation) |

---

## Queries (PostgreSQL Reporting)

### Full Study Schedule
```sql
SELECT
  t.timepoint,
  t.days_from_baseline,
  t.window_days_before,
  t.window_days_after,
  t.phase,
  t.applicable_arms
FROM doc_study_timepoint t
WHERE t.study_id = 'DEMO-001'
ORDER BY t.days_from_baseline NULLS LAST;
```

### Events at a Timepoint
```sql
SELECT
  e.event_type,
  e.description,
  e.is_required,
  e.applicable_arms
FROM doc_study_planned_event e
WHERE e.study_id = 'DEMO-001'
  AND e.timepoint = 'BASELINE'
ORDER BY e.event_type;
```

### Lab Collections Schedule
```sql
SELECT
  t.timepoint,
  t.days_from_baseline,
  l.sample_types,
  l.fasting_required
FROM doc_study_planned_lab l
JOIN doc_study_timepoint t
  ON l.study_id = t.study_id
  AND l.timepoint = t.timepoint
WHERE l.study_id = 'DEMO-001'
ORDER BY t.days_from_baseline;
```

### Arm-Specific Events
```sql
SELECT
  t.timepoint,
  e.event_type,
  e.applicable_arms
FROM doc_study_planned_event e
JOIN doc_study_timepoint t
  ON e.study_id = t.study_id
  AND e.timepoint = t.timepoint
WHERE e.study_id = 'DEMO-001'
  AND e.applicable_arms NOT LIKE '%ALL%'
ORDER BY t.days_from_baseline, e.event_type;
```

---

## Future Extensions

### Subject Execution Layer

This use case models the **plan** (protocol design). A future extension could add:

```
SUBJECT
  └── subject_id, study_id, arm, enrollment_date

SUBJECT_EVENT
  └── subject_id, timepoint, event_type (identity)
  └── planned_date, actual_date, status
  └── References the plan for validation
```

The plan provides the "what should happen," execution tracks "what did happen."

### Protocol Amendments

Studies often have amendments (version changes). Could model as:
- STUDY_DEFINITION with version field
- Template versioning captures protocol changes
- Historical timepoints preserved via document versioning

### Conditional Events

Some events are conditional (e.g., "if adverse event, schedule unplanned visit"):
- Add `trigger_condition` field to planned events
- Mark as `is_conditional: true`
- Downstream logic interprets conditions

---

## File Structure

```
docs/use-cases/study-plan/
├── README.md           # This file
├── __init__.py         # Module init
├── terminologies.py    # EVENT_TYPE, STUDY_PHASE, per-study terminologies
├── templates.py        # All template definitions
├── demo_data.py        # DEMO-001 study plan
├── seed.py             # Seed script
└── requirements.txt    # Dependencies
```

---

## Usage

```bash
cd docs/use-cases/study-plan
pip install -r requirements.txt

# Seed everything
python seed.py --base-url http://localhost

# Seed specific parts
python seed.py --terminologies
python seed.py --templates
python seed.py --data

# Dry run
python seed.py --dry-run
```
