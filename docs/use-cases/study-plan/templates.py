"""
Study Plan Use Case - Template Definitions

Defines templates for clinical study planning.
"""

TEMPLATES = [
    # =========================================================================
    # STUDY DEFINITION
    # =========================================================================
    {
        "code": "STUDY_DEFINITION",
        "name": "Study Definition",
        "description": "Core study protocol information",
        "identity_fields": ["study_id"],
        "fields": [
            {
                "name": "study_id",
                "type": "string",
                "required": True,
                "description": "Unique study identifier (e.g., DEMO-001)",
            },
            {
                "name": "name",
                "type": "string",
                "required": True,
                "description": "Full study name/title",
            },
            {
                "name": "short_name",
                "type": "string",
                "description": "Short name or acronym",
            },
            {
                "name": "description",
                "type": "string",
                "description": "Study description/summary",
            },
            {
                "name": "phase",
                "type": "string",
                "description": "Clinical phase (Phase 1, Phase 2, Phase 3, Phase 4)",
            },
            {
                "name": "sponsor",
                "type": "string",
                "description": "Study sponsor organization",
            },
            {
                "name": "therapeutic_area",
                "type": "string",
                "description": "Therapeutic area (Oncology, Cardiology, etc.)",
            },
            {
                "name": "indication",
                "type": "string",
                "description": "Target indication/disease",
            },
            {
                "name": "study_type",
                "type": "string",
                "description": "Interventional, Observational, etc.",
            },
            {
                "name": "blinding",
                "type": "string",
                "description": "Open-label, Single-blind, Double-blind",
            },
            {
                "name": "randomized",
                "type": "boolean",
                "description": "Is the study randomized?",
            },
            {
                "name": "target_enrollment",
                "type": "integer",
                "description": "Target number of subjects",
            },
            {
                "name": "duration_weeks",
                "type": "integer",
                "description": "Planned study duration in weeks",
            },
            {
                "name": "arms_terminology",
                "type": "string",
                "description": "Code of the terminology containing study arms",
            },
            {
                "name": "timepoints_terminology",
                "type": "string",
                "description": "Code of the terminology containing study timepoints",
            },
            {
                "name": "protocol_version",
                "type": "string",
                "description": "Current protocol version",
            },
            {
                "name": "protocol_date",
                "type": "date",
                "description": "Protocol version date",
            },
        ],
    },

    # =========================================================================
    # STUDY ARM
    # =========================================================================
    {
        "code": "STUDY_ARM",
        "name": "Study Arm",
        "description": "Treatment arm/group within a study",
        "identity_fields": ["study_id", "arm_code"],
        "fields": [
            {
                "name": "study_id",
                "type": "string",
                "required": True,
                "description": "Parent study identifier",
            },
            {
                "name": "arm_code",
                "type": "string",
                "required": True,
                "description": "Arm code (must match term in study arms terminology)",
            },
            {
                "name": "name",
                "type": "string",
                "required": True,
                "description": "Arm display name",
            },
            {
                "name": "description",
                "type": "string",
                "description": "Detailed arm description",
            },
            {
                "name": "randomization_ratio",
                "type": "integer",
                "description": "Randomization ratio (e.g., 1 for 1:2 ratio)",
            },
            {
                "name": "is_control",
                "type": "boolean",
                "description": "Is this the control arm?",
            },
            {
                "name": "treatment_description",
                "type": "string",
                "description": "Description of treatment in this arm",
            },
            {
                "name": "dose",
                "type": "string",
                "description": "Dose information (e.g., '100mg daily')",
            },
        ],
    },

    # =========================================================================
    # STUDY TIMEPOINT
    # =========================================================================
    {
        "code": "STUDY_TIMEPOINT",
        "name": "Study Timepoint",
        "description": "A scheduled timepoint in the study",
        "identity_fields": ["study_id", "timepoint"],
        "fields": [
            {
                "name": "study_id",
                "type": "string",
                "required": True,
                "description": "Parent study identifier",
            },
            {
                "name": "timepoint",
                "type": "string",
                "required": True,
                "description": "Timepoint code (from study timepoints terminology)",
            },
            {
                "name": "name",
                "type": "string",
                "required": True,
                "description": "Timepoint display name",
            },
            {
                "name": "days_from_baseline",
                "type": "integer",
                "description": "Days relative to baseline (Day 0). Negative for pre-baseline.",
            },
            {
                "name": "hours_from_baseline",
                "type": "integer",
                "description": "Hours offset within the day (for same-day ordering)",
            },
            {
                "name": "window_days_before",
                "type": "integer",
                "description": "Acceptable window - days before target",
            },
            {
                "name": "window_days_after",
                "type": "integer",
                "description": "Acceptable window - days after target",
            },
            {
                "name": "phase",
                "type": "term",
                "terminology_ref": "STUDY_PHASE",
                "description": "Study phase this timepoint belongs to",
            },
            {
                "name": "applicable_arms",
                "type": "string",
                "description": "Comma-separated arm codes, or 'ALL' for all arms",
            },
            {
                "name": "is_required",
                "type": "boolean",
                "description": "Is this timepoint mandatory?",
            },
            {
                "name": "is_anchor",
                "type": "boolean",
                "description": "Is this the Day 0 anchor point?",
            },
            {
                "name": "description",
                "type": "string",
                "description": "Additional notes about this timepoint",
            },
        ],
    },

    # =========================================================================
    # STUDY PLANNED EVENT (BASE)
    # =========================================================================
    {
        "code": "STUDY_PLANNED_EVENT",
        "name": "Study Planned Event",
        "description": "Base template for planned study events",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "study_id",
                "type": "string",
                "required": True,
                "description": "Parent study identifier",
            },
            {
                "name": "timepoint",
                "type": "string",
                "required": True,
                "description": "Timepoint code when this event occurs",
            },
            {
                "name": "event_type",
                "type": "term",
                "terminology_ref": "EVENT_TYPE",
                "required": True,
                "description": "Type of event",
            },
            {
                "name": "applicable_arms",
                "type": "string",
                "description": "Comma-separated arm codes, or 'ALL' for all arms",
            },
            {
                "name": "description",
                "type": "string",
                "description": "Event description",
            },
            {
                "name": "is_required",
                "type": "boolean",
                "description": "Is this event mandatory?",
            },
            {
                "name": "sequence_order",
                "type": "integer",
                "description": "Order within timepoint (for multiple events)",
            },
        ],
    },

    # =========================================================================
    # SPECIALIZED EVENT TEMPLATES
    # =========================================================================
    {
        "code": "STUDY_PLANNED_VISIT",
        "name": "Planned Site Visit",
        "description": "In-person visit at study site",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "location_type",
                "type": "string",
                "description": "clinic, hospital, home, etc.",
            },
            {
                "name": "estimated_duration_minutes",
                "type": "integer",
                "description": "Expected visit duration",
            },
            {
                "name": "fasting_required",
                "type": "boolean",
                "description": "Is fasting required for this visit?",
            },
            {
                "name": "overnight_stay",
                "type": "boolean",
                "description": "Does this visit require overnight stay?",
            },
            {
                "name": "special_instructions",
                "type": "string",
                "description": "Special instructions for subjects",
            },
        ],
    },
    {
        "code": "STUDY_PLANNED_LAB",
        "name": "Planned Lab Collection",
        "description": "Biological sample collection",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "sample_types",
                "type": "string",
                "description": "Comma-separated sample types (serum, plasma, urine)",
            },
            {
                "name": "tube_types",
                "type": "string",
                "description": "Required tube types",
            },
            {
                "name": "total_volume_ml",
                "type": "number",
                "description": "Total blood volume to collect (mL)",
            },
            {
                "name": "fasting_required",
                "type": "boolean",
                "description": "Is fasting required?",
            },
            {
                "name": "fasting_hours",
                "type": "integer",
                "description": "Minimum fasting hours if required",
            },
            {
                "name": "processing_instructions",
                "type": "string",
                "description": "Sample processing notes",
            },
        ],
    },
    {
        "code": "STUDY_PLANNED_QUESTIONNAIRE",
        "name": "Planned Questionnaire",
        "description": "Patient-reported outcome or assessment questionnaire",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "form_name",
                "type": "string",
                "description": "Name of the questionnaire/form",
            },
            {
                "name": "form_version",
                "type": "string",
                "description": "Form version",
            },
            {
                "name": "estimated_minutes",
                "type": "integer",
                "description": "Estimated completion time",
            },
            {
                "name": "administration_method",
                "type": "string",
                "description": "Paper, electronic, interview, etc.",
            },
            {
                "name": "recall_period",
                "type": "string",
                "description": "Recall period (e.g., 'past 7 days', 'past 24 hours')",
            },
        ],
    },
    {
        "code": "STUDY_PLANNED_IMAGING",
        "name": "Planned Imaging",
        "description": "Medical imaging procedure",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "imaging_type",
                "type": "term",
                "terminology_ref": "IMAGING_TYPE",
                "description": "Type of imaging",
            },
            {
                "name": "body_region",
                "type": "string",
                "description": "Body region to image",
            },
            {
                "name": "contrast_required",
                "type": "boolean",
                "description": "Is contrast agent required?",
            },
            {
                "name": "estimated_duration_minutes",
                "type": "integer",
                "description": "Expected procedure duration",
            },
            {
                "name": "preparation_instructions",
                "type": "string",
                "description": "Patient preparation instructions",
            },
        ],
    },
    {
        "code": "STUDY_PLANNED_PHONE_CALL",
        "name": "Planned Phone Call",
        "description": "Scheduled telephone contact",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "call_purpose",
                "type": "string",
                "description": "Purpose of the call (safety check, reminder, etc.)",
            },
            {
                "name": "estimated_duration_minutes",
                "type": "integer",
                "description": "Expected call duration",
            },
            {
                "name": "topics_to_cover",
                "type": "string",
                "description": "Topics/questions to cover during call",
            },
        ],
    },
    {
        "code": "STUDY_PLANNED_PROCEDURE",
        "name": "Planned Procedure",
        "description": "Medical procedure (ECG, biopsy, etc.)",
        "extends": "STUDY_PLANNED_EVENT",
        "identity_fields": ["study_id", "timepoint", "event_type"],
        "fields": [
            {
                "name": "procedure_name",
                "type": "string",
                "description": "Name of the procedure",
            },
            {
                "name": "requires_consent",
                "type": "boolean",
                "description": "Does this procedure require separate consent?",
            },
            {
                "name": "estimated_duration_minutes",
                "type": "integer",
                "description": "Expected procedure duration",
            },
            {
                "name": "recovery_time_minutes",
                "type": "integer",
                "description": "Expected recovery/observation time",
            },
            {
                "name": "preparation_instructions",
                "type": "string",
                "description": "Pre-procedure instructions",
            },
        ],
    },
]
