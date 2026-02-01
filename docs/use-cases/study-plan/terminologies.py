"""
Study Plan Use Case - Terminology Definitions

Defines global terminologies and per-study terminologies for clinical study planning.
"""

# =============================================================================
# GLOBAL TERMINOLOGIES (shared across all studies)
# =============================================================================

GLOBAL_TERMINOLOGIES = [
    {
        "code": "EVENT_TYPE",
        "name": "Study Event Type",
        "description": "Types of events that can occur in a clinical study",
        "terms": [
            {
                "code": "VISIT",
                "value": "visit",
                "label": "Site Visit",
                "sort_order": 1,
                "description": "In-person visit at study site",
            },
            {
                "code": "LAB_COLLECTION",
                "value": "lab_collection",
                "label": "Lab Collection",
                "sort_order": 2,
                "description": "Blood, urine, or other sample collection",
            },
            {
                "code": "QUESTIONNAIRE",
                "value": "questionnaire",
                "label": "Questionnaire",
                "sort_order": 3,
                "description": "Patient-reported outcomes or assessments",
            },
            {
                "code": "IMAGING",
                "value": "imaging",
                "label": "Imaging",
                "sort_order": 4,
                "description": "MRI, CT, X-ray, ultrasound, etc.",
            },
            {
                "code": "PHONE_CALL",
                "value": "phone_call",
                "label": "Phone Call",
                "sort_order": 5,
                "description": "Telephone contact with subject",
            },
            {
                "code": "PROCEDURE",
                "value": "procedure",
                "label": "Procedure",
                "sort_order": 6,
                "description": "Medical procedure (biopsy, ECG, etc.)",
            },
            {
                "code": "DRUG_ADMIN",
                "value": "drug_admin",
                "label": "Drug Administration",
                "sort_order": 7,
                "description": "Study drug dosing",
            },
            {
                "code": "INFORMED_CONSENT",
                "value": "informed_consent",
                "label": "Informed Consent",
                "sort_order": 8,
                "description": "Consent process and documentation",
            },
            {
                "code": "PHYSICAL_EXAM",
                "value": "physical_exam",
                "label": "Physical Examination",
                "sort_order": 9,
                "description": "Physical examination by investigator",
            },
            {
                "code": "VITAL_SIGNS",
                "value": "vital_signs",
                "label": "Vital Signs",
                "sort_order": 10,
                "description": "Blood pressure, heart rate, temperature, etc.",
            },
            {
                "code": "ADVERSE_EVENT_CHECK",
                "value": "adverse_event_check",
                "label": "Adverse Event Assessment",
                "sort_order": 11,
                "description": "Review and documentation of adverse events",
            },
        ],
    },
    {
        "code": "STUDY_PHASE",
        "name": "Study Phase",
        "description": "Phases within a clinical study",
        "terms": [
            {
                "code": "SCREENING",
                "value": "screening",
                "label": "Screening",
                "sort_order": 1,
                "description": "Eligibility assessment before enrollment",
            },
            {
                "code": "BASELINE",
                "value": "baseline",
                "label": "Baseline",
                "sort_order": 2,
                "description": "Day 0 baseline assessments",
            },
            {
                "code": "TREATMENT",
                "value": "treatment",
                "label": "Treatment",
                "sort_order": 3,
                "description": "Active treatment period",
            },
            {
                "code": "FOLLOW_UP",
                "value": "follow_up",
                "label": "Follow-up",
                "sort_order": 4,
                "description": "Post-treatment follow-up period",
            },
            {
                "code": "EARLY_TERMINATION",
                "value": "early_termination",
                "label": "Early Termination",
                "sort_order": 5,
                "description": "Early discontinuation from study",
            },
        ],
    },
    {
        "code": "SAMPLE_TYPE",
        "name": "Sample Type",
        "description": "Types of biological samples collected",
        "terms": [
            {"code": "BLOOD_SERUM", "value": "blood_serum", "label": "Blood (Serum)", "sort_order": 1},
            {"code": "BLOOD_PLASMA", "value": "blood_plasma", "label": "Blood (Plasma)", "sort_order": 2},
            {"code": "BLOOD_WHOLE", "value": "blood_whole", "label": "Blood (Whole)", "sort_order": 3},
            {"code": "URINE", "value": "urine", "label": "Urine", "sort_order": 4},
            {"code": "SALIVA", "value": "saliva", "label": "Saliva", "sort_order": 5},
            {"code": "STOOL", "value": "stool", "label": "Stool", "sort_order": 6},
            {"code": "CSF", "value": "csf", "label": "Cerebrospinal Fluid", "sort_order": 7},
            {"code": "TISSUE", "value": "tissue", "label": "Tissue Biopsy", "sort_order": 8},
        ],
    },
    {
        "code": "IMAGING_TYPE",
        "name": "Imaging Type",
        "description": "Types of medical imaging",
        "terms": [
            {"code": "MRI", "value": "mri", "label": "MRI", "sort_order": 1},
            {"code": "CT", "value": "ct", "label": "CT Scan", "sort_order": 2},
            {"code": "XRAY", "value": "xray", "label": "X-Ray", "sort_order": 3},
            {"code": "ULTRASOUND", "value": "ultrasound", "label": "Ultrasound", "sort_order": 4},
            {"code": "PET", "value": "pet", "label": "PET Scan", "sort_order": 5},
            {"code": "DEXA", "value": "dexa", "label": "DEXA Scan", "sort_order": 6},
            {"code": "ECG", "value": "ecg", "label": "ECG/EKG", "sort_order": 7},
            {"code": "ECHO", "value": "echo", "label": "Echocardiogram", "sort_order": 8},
        ],
    },
]


# =============================================================================
# PER-STUDY TERMINOLOGIES (DEMO-001 example)
# =============================================================================

def get_study_terminologies(study_id: str) -> list:
    """
    Get study-specific terminologies.

    In a real system, these would be generated dynamically based on
    the study protocol. For the demo, we return DEMO-001 definitions.
    """
    if study_id == "DEMO-001":
        return DEMO_001_TERMINOLOGIES
    return []


DEMO_001_TERMINOLOGIES = [
    {
        "code": "STUDY_DEMO001_ARMS",
        "name": "DEMO-001 Study Arms",
        "description": "Treatment arms for study DEMO-001",
        "terms": [
            {
                "code": "PLACEBO",
                "value": "placebo",
                "label": "Placebo",
                "sort_order": 1,
                "description": "Placebo control arm",
                "metadata": {
                    "randomization_ratio": 1,
                    "is_control": True,
                },
            },
            {
                "code": "TREATMENT",
                "value": "treatment",
                "label": "Treatment 100mg",
                "sort_order": 2,
                "description": "Active treatment arm - 100mg daily",
                "metadata": {
                    "randomization_ratio": 2,
                    "is_control": False,
                    "dose_mg": 100,
                    "frequency": "daily",
                },
            },
        ],
    },
    {
        "code": "STUDY_DEMO001_TIMEPOINTS",
        "name": "DEMO-001 Timepoints",
        "description": "Visit timepoints for study DEMO-001",
        "terms": [
            {
                "code": "SCREENING",
                "value": "screening",
                "label": "Screening",
                "sort_order": 1,
                "description": "Screening visit - eligibility assessment",
                "metadata": {
                    "days_from_baseline": -14,
                    "window_days_before": 3,
                    "window_days_after": 3,
                    "phase": "screening",
                },
            },
            {
                "code": "BASELINE",
                "value": "baseline",
                "label": "Baseline (Day 0)",
                "sort_order": 2,
                "description": "Baseline visit - Day 0, randomization",
                "metadata": {
                    "days_from_baseline": 0,
                    "window_days_before": 0,
                    "window_days_after": 0,
                    "phase": "baseline",
                    "is_anchor": True,
                },
            },
            {
                "code": "WEEK_2",
                "value": "week_2",
                "label": "Week 2",
                "sort_order": 3,
                "description": "Week 2 safety phone call",
                "metadata": {
                    "days_from_baseline": 14,
                    "window_days_before": 2,
                    "window_days_after": 2,
                    "phase": "treatment",
                },
            },
            {
                "code": "WEEK_4",
                "value": "week_4",
                "label": "Week 4",
                "sort_order": 4,
                "description": "Week 4 visit",
                "metadata": {
                    "days_from_baseline": 28,
                    "window_days_before": 3,
                    "window_days_after": 3,
                    "phase": "treatment",
                },
            },
            {
                "code": "WEEK_8",
                "value": "week_8",
                "label": "Week 8",
                "sort_order": 5,
                "description": "Week 8 visit - interim assessment",
                "metadata": {
                    "days_from_baseline": 56,
                    "window_days_before": 3,
                    "window_days_after": 3,
                    "phase": "treatment",
                },
            },
            {
                "code": "END_OF_STUDY",
                "value": "end_of_study",
                "label": "End of Study (Week 12)",
                "sort_order": 6,
                "description": "End of study visit - final assessments",
                "metadata": {
                    "days_from_baseline": 84,
                    "window_days_before": 7,
                    "window_days_after": 7,
                    "phase": "follow_up",
                },
            },
            {
                "code": "EARLY_TERM",
                "value": "early_term",
                "label": "Early Termination",
                "sort_order": 99,
                "description": "Early termination visit - for subjects discontinuing early",
                "metadata": {
                    "days_from_baseline": None,  # No fixed day
                    "window_days_before": 0,
                    "window_days_after": 7,
                    "phase": "early_termination",
                    "is_unscheduled": True,
                },
            },
        ],
    },
]


# Combined list for seeding
ALL_TERMINOLOGIES = GLOBAL_TERMINOLOGIES + DEMO_001_TERMINOLOGIES
