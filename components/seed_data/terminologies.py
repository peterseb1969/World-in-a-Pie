"""
Terminology definitions for comprehensive testing.

Contains 15 terminologies covering various use cases:
- Basic validation (SALUTATION, GENDER, MARITAL_STATUS)
- Large sets (COUNTRY, CURRENCY, LANGUAGE)
- Hierarchical (DEPARTMENT)
- Workflow (DOC_STATUS, PRIORITY, SEVERITY)
- E-commerce (PRODUCT_CATEGORY, PAYMENT_METHOD, UNIT_OF_MEASURE)
- HR (EMPLOYMENT_TYPE)

- Healthcare (BLOOD_TYPE)
"""
from __future__ import annotations

from typing import Any


def get_terminology_definitions() -> list[dict[str, Any]]:
    """Return all terminology definitions."""
    return [
        SALUTATION,
        GENDER,
        COUNTRY,
        CURRENCY,
        LANGUAGE,
        DOC_STATUS,
        PRIORITY,
        DEPARTMENT,
        PRODUCT_CATEGORY,
        PAYMENT_METHOD,
        EMPLOYMENT_TYPE,
        MARITAL_STATUS,
        BLOOD_TYPE,
        UNIT_OF_MEASURE,
        SEVERITY,
    ]


def get_terminology_by_code(code: str) -> dict[str, Any] | None:
    """Get a specific terminology by code."""
    for term in get_terminology_definitions():
        if term["code"] == code:
            return term
    return None


# =============================================================================
# SALUTATION - Test aliases (multiple values resolve to same term)
# =============================================================================
SALUTATION = {
    "code": "SALUTATION",
    "name": "Salutations",
    "description": "Common salutations and titles for addressing people",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {
            "code": "MR",
            "value": "Mr",
            "label": "Mister",
            "aliases": ["MR.", "Mr.", "mr", "mr.", "mister", "MISTER"],
            "sort_order": 1,
            "translations": [
                {"language": "de", "value": "Herr", "label": "Herr"},
                {"language": "fr", "value": "M.", "label": "Monsieur"}
            ]
        },
        {
            "code": "MRS",
            "value": "Mrs",
            "label": "Missus",
            "aliases": ["MRS.", "Mrs.", "mrs", "mrs.", "missus", "MISSUS"],
            "sort_order": 2,
            "translations": [
                {"language": "de", "value": "Frau", "label": "Frau"},
                {"language": "fr", "value": "Mme", "label": "Madame"}
            ]
        },
        {
            "code": "MS",
            "value": "Ms",
            "label": "Ms",
            "aliases": ["MS.", "Ms.", "ms", "ms."],
            "sort_order": 3,
            "translations": [
                {"language": "de", "value": "Frau", "label": "Frau"},
                {"language": "fr", "value": "Mlle", "label": "Mademoiselle"}
            ]
        },
        {
            "code": "DR",
            "value": "Dr",
            "label": "Doctor",
            "aliases": ["DR.", "Dr.", "dr", "dr.", "doctor", "DOCTOR"],
            "sort_order": 4,
            "translations": [
                {"language": "de", "value": "Dr.", "label": "Doktor"},
                {"language": "fr", "value": "Dr", "label": "Docteur"}
            ]
        },
        {
            "code": "PROF",
            "value": "Prof",
            "label": "Professor",
            "aliases": ["PROF.", "Prof.", "prof", "prof.", "professor", "PROFESSOR"],
            "sort_order": 5,
            "translations": [
                {"language": "de", "value": "Prof.", "label": "Professor"},
                {"language": "fr", "value": "Pr", "label": "Professeur"}
            ]
        }
    ]
}


# =============================================================================
# GENDER - Basic term validation
# =============================================================================
GENDER = {
    "code": "GENDER",
    "name": "Gender",
    "description": "Gender identity options",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {
            "code": "M",
            "value": "Male",
            "label": "Male",
            "aliases": ["male", "MALE", "m", "M"],
            "sort_order": 1,
            "translations": [
                {"language": "de", "value": "Maennlich", "label": "Maennlich"},
                {"language": "fr", "value": "Masculin", "label": "Masculin"}
            ]
        },
        {
            "code": "F",
            "value": "Female",
            "label": "Female",
            "aliases": ["female", "FEMALE", "f", "F"],
            "sort_order": 2,
            "translations": [
                {"language": "de", "value": "Weiblich", "label": "Weiblich"},
                {"language": "fr", "value": "Feminin", "label": "Feminin"}
            ]
        },
        {
            "code": "NB",
            "value": "Non-binary",
            "label": "Non-binary",
            "aliases": ["nonbinary", "non-binary", "nb", "NB"],
            "sort_order": 3
        },
        {
            "code": "U",
            "value": "Unspecified",
            "label": "Prefer not to say",
            "aliases": ["unspecified", "unknown", "U", "u"],
            "sort_order": 4
        }
    ]
}


# =============================================================================
# COUNTRY - Large terminology (ISO 3166-1 alpha-3)
# =============================================================================
COUNTRY = {
    "code": "COUNTRY",
    "name": "Countries",
    "description": "ISO 3166-1 country codes",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "ISO 3166-1",
        "version": "2024",
        "language": "en"
    },
    "terms": [
        # Major countries first
        {"code": "USA", "value": "United States", "label": "United States of America", "aliases": ["US", "United States of America", "America", "USA"], "sort_order": 1},
        {"code": "GBR", "value": "United Kingdom", "label": "United Kingdom", "aliases": ["UK", "GB", "Great Britain", "England", "Britain"], "sort_order": 2},
        {"code": "DEU", "value": "Germany", "label": "Germany", "aliases": ["DE", "Deutschland"], "sort_order": 3},
        {"code": "FRA", "value": "France", "label": "France", "aliases": ["FR"], "sort_order": 4},
        {"code": "JPN", "value": "Japan", "label": "Japan", "aliases": ["JP", "Nippon"], "sort_order": 5},
        {"code": "CHN", "value": "China", "label": "China", "aliases": ["CN", "PRC"], "sort_order": 6},
        {"code": "IND", "value": "India", "label": "India", "aliases": ["IN"], "sort_order": 7},
        {"code": "BRA", "value": "Brazil", "label": "Brazil", "aliases": ["BR", "Brasil"], "sort_order": 8},
        {"code": "CAN", "value": "Canada", "label": "Canada", "aliases": ["CA"], "sort_order": 9},
        {"code": "AUS", "value": "Australia", "label": "Australia", "aliases": ["AU"], "sort_order": 10},
        # Europe
        {"code": "ITA", "value": "Italy", "label": "Italy", "aliases": ["IT", "Italia"], "sort_order": 11},
        {"code": "ESP", "value": "Spain", "label": "Spain", "aliases": ["ES", "Espana"], "sort_order": 12},
        {"code": "NLD", "value": "Netherlands", "label": "Netherlands", "aliases": ["NL", "Holland"], "sort_order": 13},
        {"code": "BEL", "value": "Belgium", "label": "Belgium", "aliases": ["BE"], "sort_order": 14},
        {"code": "CHE", "value": "Switzerland", "label": "Switzerland", "aliases": ["CH", "Schweiz", "Suisse"], "sort_order": 15},
        {"code": "AUT", "value": "Austria", "label": "Austria", "aliases": ["AT", "Oesterreich"], "sort_order": 16},
        {"code": "SWE", "value": "Sweden", "label": "Sweden", "aliases": ["SE", "Sverige"], "sort_order": 17},
        {"code": "NOR", "value": "Norway", "label": "Norway", "aliases": ["NO", "Norge"], "sort_order": 18},
        {"code": "DNK", "value": "Denmark", "label": "Denmark", "aliases": ["DK", "Danmark"], "sort_order": 19},
        {"code": "FIN", "value": "Finland", "label": "Finland", "aliases": ["FI", "Suomi"], "sort_order": 20},
        {"code": "POL", "value": "Poland", "label": "Poland", "aliases": ["PL", "Polska"], "sort_order": 21},
        {"code": "CZE", "value": "Czech Republic", "label": "Czech Republic", "aliases": ["CZ", "Czechia"], "sort_order": 22},
        {"code": "PRT", "value": "Portugal", "label": "Portugal", "aliases": ["PT"], "sort_order": 23},
        {"code": "GRC", "value": "Greece", "label": "Greece", "aliases": ["GR", "Hellas"], "sort_order": 24},
        {"code": "IRL", "value": "Ireland", "label": "Ireland", "aliases": ["IE", "Eire"], "sort_order": 25},
        {"code": "HUN", "value": "Hungary", "label": "Hungary", "aliases": ["HU", "Magyarorszag"], "sort_order": 26},
        {"code": "ROU", "value": "Romania", "label": "Romania", "aliases": ["RO"], "sort_order": 27},
        {"code": "BGR", "value": "Bulgaria", "label": "Bulgaria", "aliases": ["BG"], "sort_order": 28},
        {"code": "HRV", "value": "Croatia", "label": "Croatia", "aliases": ["HR", "Hrvatska"], "sort_order": 29},
        {"code": "SVK", "value": "Slovakia", "label": "Slovakia", "aliases": ["SK"], "sort_order": 30},
        # Asia
        {"code": "KOR", "value": "South Korea", "label": "South Korea", "aliases": ["KR", "Korea"], "sort_order": 31},
        {"code": "SGP", "value": "Singapore", "label": "Singapore", "aliases": ["SG"], "sort_order": 32},
        {"code": "THA", "value": "Thailand", "label": "Thailand", "aliases": ["TH"], "sort_order": 33},
        {"code": "VNM", "value": "Vietnam", "label": "Vietnam", "aliases": ["VN"], "sort_order": 34},
        {"code": "IDN", "value": "Indonesia", "label": "Indonesia", "aliases": ["ID"], "sort_order": 35},
        {"code": "MYS", "value": "Malaysia", "label": "Malaysia", "aliases": ["MY"], "sort_order": 36},
        {"code": "PHL", "value": "Philippines", "label": "Philippines", "aliases": ["PH"], "sort_order": 37},
        {"code": "TWN", "value": "Taiwan", "label": "Taiwan", "aliases": ["TW"], "sort_order": 38},
        {"code": "HKG", "value": "Hong Kong", "label": "Hong Kong", "aliases": ["HK"], "sort_order": 39},
        {"code": "PAK", "value": "Pakistan", "label": "Pakistan", "aliases": ["PK"], "sort_order": 40},
        # Americas
        {"code": "MEX", "value": "Mexico", "label": "Mexico", "aliases": ["MX", "Mejico"], "sort_order": 41},
        {"code": "ARG", "value": "Argentina", "label": "Argentina", "aliases": ["AR"], "sort_order": 42},
        {"code": "COL", "value": "Colombia", "label": "Colombia", "aliases": ["CO"], "sort_order": 43},
        {"code": "CHL", "value": "Chile", "label": "Chile", "aliases": ["CL"], "sort_order": 44},
        {"code": "PER", "value": "Peru", "label": "Peru", "aliases": ["PE"], "sort_order": 45},
        # Middle East / Africa
        {"code": "ISR", "value": "Israel", "label": "Israel", "aliases": ["IL"], "sort_order": 46},
        {"code": "ARE", "value": "United Arab Emirates", "label": "United Arab Emirates", "aliases": ["AE", "UAE"], "sort_order": 47},
        {"code": "SAU", "value": "Saudi Arabia", "label": "Saudi Arabia", "aliases": ["SA"], "sort_order": 48},
        {"code": "ZAF", "value": "South Africa", "label": "South Africa", "aliases": ["ZA"], "sort_order": 49},
        {"code": "EGY", "value": "Egypt", "label": "Egypt", "aliases": ["EG"], "sort_order": 50},
        # Oceania
        {"code": "NZL", "value": "New Zealand", "label": "New Zealand", "aliases": ["NZ"], "sort_order": 51},
        # Russia and neighbors
        {"code": "RUS", "value": "Russia", "label": "Russia", "aliases": ["RU"], "sort_order": 52},
        {"code": "UKR", "value": "Ukraine", "label": "Ukraine", "aliases": ["UA"], "sort_order": 53},
        {"code": "TUR", "value": "Turkey", "label": "Turkey", "aliases": ["TR", "Turkiye"], "sort_order": 54},
    ]
}


# =============================================================================
# CURRENCY - Financial use case
# =============================================================================
CURRENCY = {
    "code": "CURRENCY",
    "name": "Currencies",
    "description": "ISO 4217 currency codes",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "ISO 4217",
        "version": "2024",
        "language": "en"
    },
    "terms": [
        {"code": "USD", "value": "US Dollar", "label": "US Dollar", "aliases": ["$", "dollar", "dollars"], "sort_order": 1, "metadata": {"symbol": "$", "decimals": 2}},
        {"code": "EUR", "value": "Euro", "label": "Euro", "aliases": ["euro", "euros"], "sort_order": 2, "metadata": {"symbol": "\u20ac", "decimals": 2}},
        {"code": "GBP", "value": "British Pound", "label": "British Pound Sterling", "aliases": ["pound", "pounds", "sterling"], "sort_order": 3, "metadata": {"symbol": "\u00a3", "decimals": 2}},
        {"code": "JPY", "value": "Japanese Yen", "label": "Japanese Yen", "aliases": ["yen"], "sort_order": 4, "metadata": {"symbol": "\u00a5", "decimals": 0}},
        {"code": "CHF", "value": "Swiss Franc", "label": "Swiss Franc", "aliases": ["franc", "francs"], "sort_order": 5, "metadata": {"symbol": "CHF", "decimals": 2}},
        {"code": "CNY", "value": "Chinese Yuan", "label": "Chinese Yuan Renminbi", "aliases": ["yuan", "renminbi", "RMB"], "sort_order": 6, "metadata": {"symbol": "\u00a5", "decimals": 2}},
        {"code": "AUD", "value": "Australian Dollar", "label": "Australian Dollar", "aliases": ["AUD"], "sort_order": 7, "metadata": {"symbol": "A$", "decimals": 2}},
        {"code": "CAD", "value": "Canadian Dollar", "label": "Canadian Dollar", "aliases": ["CAD"], "sort_order": 8, "metadata": {"symbol": "C$", "decimals": 2}},
        {"code": "INR", "value": "Indian Rupee", "label": "Indian Rupee", "aliases": ["rupee", "rupees"], "sort_order": 9, "metadata": {"symbol": "\u20b9", "decimals": 2}},
        {"code": "BRL", "value": "Brazilian Real", "label": "Brazilian Real", "aliases": ["real", "reais"], "sort_order": 10, "metadata": {"symbol": "R$", "decimals": 2}},
        {"code": "KRW", "value": "South Korean Won", "label": "South Korean Won", "aliases": ["won"], "sort_order": 11, "metadata": {"symbol": "\u20a9", "decimals": 0}},
        {"code": "SEK", "value": "Swedish Krona", "label": "Swedish Krona", "aliases": ["krona", "kronor"], "sort_order": 12, "metadata": {"symbol": "kr", "decimals": 2}},
        {"code": "NOK", "value": "Norwegian Krone", "label": "Norwegian Krone", "aliases": ["krone", "kroner"], "sort_order": 13, "metadata": {"symbol": "kr", "decimals": 2}},
        {"code": "DKK", "value": "Danish Krone", "label": "Danish Krone", "aliases": ["DKK"], "sort_order": 14, "metadata": {"symbol": "kr", "decimals": 2}},
        {"code": "SGD", "value": "Singapore Dollar", "label": "Singapore Dollar", "aliases": ["SGD"], "sort_order": 15, "metadata": {"symbol": "S$", "decimals": 2}},
        {"code": "HKD", "value": "Hong Kong Dollar", "label": "Hong Kong Dollar", "aliases": ["HKD"], "sort_order": 16, "metadata": {"symbol": "HK$", "decimals": 2}},
        {"code": "MXN", "value": "Mexican Peso", "label": "Mexican Peso", "aliases": ["peso", "pesos"], "sort_order": 17, "metadata": {"symbol": "$", "decimals": 2}},
        {"code": "ZAR", "value": "South African Rand", "label": "South African Rand", "aliases": ["rand"], "sort_order": 18, "metadata": {"symbol": "R", "decimals": 2}},
        {"code": "PLN", "value": "Polish Zloty", "label": "Polish Zloty", "aliases": ["zloty"], "sort_order": 19, "metadata": {"symbol": "zl", "decimals": 2}},
        {"code": "CZK", "value": "Czech Koruna", "label": "Czech Koruna", "aliases": ["koruna"], "sort_order": 20, "metadata": {"symbol": "Kc", "decimals": 2}},
        {"code": "THB", "value": "Thai Baht", "label": "Thai Baht", "aliases": ["baht"], "sort_order": 21, "metadata": {"symbol": "\u0e3f", "decimals": 2}},
        {"code": "NZD", "value": "New Zealand Dollar", "label": "New Zealand Dollar", "aliases": ["NZD"], "sort_order": 22, "metadata": {"symbol": "NZ$", "decimals": 2}},
        {"code": "RUB", "value": "Russian Ruble", "label": "Russian Ruble", "aliases": ["ruble", "rubles"], "sort_order": 23, "metadata": {"symbol": "\u20bd", "decimals": 2}},
        {"code": "TRY", "value": "Turkish Lira", "label": "Turkish Lira", "aliases": ["lira"], "sort_order": 24, "metadata": {"symbol": "\u20ba", "decimals": 2}},
        {"code": "AED", "value": "UAE Dirham", "label": "United Arab Emirates Dirham", "aliases": ["dirham"], "sort_order": 25, "metadata": {"symbol": "\u062f.\u0625", "decimals": 2}},
        {"code": "SAR", "value": "Saudi Riyal", "label": "Saudi Riyal", "aliases": ["riyal"], "sort_order": 26, "metadata": {"symbol": "\ufdfc", "decimals": 2}},
        {"code": "ILS", "value": "Israeli Shekel", "label": "Israeli New Shekel", "aliases": ["shekel", "shekels"], "sort_order": 27, "metadata": {"symbol": "\u20aa", "decimals": 2}},
        {"code": "PHP", "value": "Philippine Peso", "label": "Philippine Peso", "aliases": ["PHP"], "sort_order": 28, "metadata": {"symbol": "\u20b1", "decimals": 2}},
        {"code": "IDR", "value": "Indonesian Rupiah", "label": "Indonesian Rupiah", "aliases": ["rupiah"], "sort_order": 29, "metadata": {"symbol": "Rp", "decimals": 0}},
        {"code": "MYR", "value": "Malaysian Ringgit", "label": "Malaysian Ringgit", "aliases": ["ringgit"], "sort_order": 30, "metadata": {"symbol": "RM", "decimals": 2}},
    ]
}


# =============================================================================
# LANGUAGE - Multi-language support
# =============================================================================
LANGUAGE = {
    "code": "LANGUAGE",
    "name": "Languages",
    "description": "ISO 639-1 language codes",
    "case_sensitive": False,
    "allow_multiple": True,
    "extensible": False,
    "metadata": {
        "source": "ISO 639-1",
        "version": "2024",
        "language": "en"
    },
    "terms": [
        {"code": "en", "value": "English", "label": "English", "aliases": ["eng", "english"], "sort_order": 1},
        {"code": "de", "value": "German", "label": "Deutsch", "aliases": ["deu", "german", "deutsch"], "sort_order": 2},
        {"code": "fr", "value": "French", "label": "Francais", "aliases": ["fra", "french", "francais"], "sort_order": 3},
        {"code": "es", "value": "Spanish", "label": "Espanol", "aliases": ["spa", "spanish", "espanol"], "sort_order": 4},
        {"code": "it", "value": "Italian", "label": "Italiano", "aliases": ["ita", "italian", "italiano"], "sort_order": 5},
        {"code": "pt", "value": "Portuguese", "label": "Portugues", "aliases": ["por", "portuguese"], "sort_order": 6},
        {"code": "nl", "value": "Dutch", "label": "Nederlands", "aliases": ["nld", "dutch"], "sort_order": 7},
        {"code": "ja", "value": "Japanese", "label": "Nihongo", "aliases": ["jpn", "japanese"], "sort_order": 8},
        {"code": "zh", "value": "Chinese", "label": "Zhongwen", "aliases": ["zho", "chinese", "mandarin"], "sort_order": 9},
        {"code": "ko", "value": "Korean", "label": "Hangugeo", "aliases": ["kor", "korean"], "sort_order": 10},
        {"code": "ru", "value": "Russian", "label": "Russkiy", "aliases": ["rus", "russian"], "sort_order": 11},
        {"code": "ar", "value": "Arabic", "label": "Arabiya", "aliases": ["ara", "arabic"], "sort_order": 12},
        {"code": "hi", "value": "Hindi", "label": "Hindi", "aliases": ["hin", "hindi"], "sort_order": 13},
        {"code": "pl", "value": "Polish", "label": "Polski", "aliases": ["pol", "polish"], "sort_order": 14},
        {"code": "tr", "value": "Turkish", "label": "Turkce", "aliases": ["tur", "turkish"], "sort_order": 15},
        {"code": "sv", "value": "Swedish", "label": "Svenska", "aliases": ["swe", "swedish"], "sort_order": 16},
        {"code": "da", "value": "Danish", "label": "Dansk", "aliases": ["dan", "danish"], "sort_order": 17},
        {"code": "fi", "value": "Finnish", "label": "Suomi", "aliases": ["fin", "finnish"], "sort_order": 18},
        {"code": "no", "value": "Norwegian", "label": "Norsk", "aliases": ["nor", "norwegian"], "sort_order": 19},
        {"code": "cs", "value": "Czech", "label": "Cestina", "aliases": ["ces", "czech"], "sort_order": 20},
    ]
}


# =============================================================================
# DOC_STATUS - Workflow states
# =============================================================================
DOC_STATUS = {
    "code": "DOC_STATUS",
    "name": "Document Status",
    "description": "Document lifecycle status values",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "DRAFT", "value": "Draft", "label": "Draft", "aliases": ["draft"], "sort_order": 1, "metadata": {"color": "#808080", "icon": "draft"}},
        {"code": "PENDING", "value": "Pending Review", "label": "Pending Review", "aliases": ["pending", "review"], "sort_order": 2, "metadata": {"color": "#FFA500", "icon": "clock"}},
        {"code": "APPROVED", "value": "Approved", "label": "Approved", "aliases": ["approved"], "sort_order": 3, "metadata": {"color": "#008000", "icon": "check"}},
        {"code": "REJECTED", "value": "Rejected", "label": "Rejected", "aliases": ["rejected", "declined"], "sort_order": 4, "metadata": {"color": "#FF0000", "icon": "x"}},
        {"code": "ARCHIVED", "value": "Archived", "label": "Archived", "aliases": ["archived"], "sort_order": 5, "metadata": {"color": "#6c757d", "icon": "archive"}},
    ]
}


# =============================================================================
# PRIORITY - Priority levels with sort_order
# =============================================================================
PRIORITY = {
    "code": "PRIORITY",
    "name": "Priority Levels",
    "description": "Task and issue priority levels",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "CRITICAL", "value": "Critical", "label": "Critical", "aliases": ["P0", "critical", "blocker"], "sort_order": 1, "metadata": {"color": "#DC143C", "weight": 100}},
        {"code": "HIGH", "value": "High", "label": "High Priority", "aliases": ["P1", "high", "urgent"], "sort_order": 2, "metadata": {"color": "#FF4500", "weight": 75}},
        {"code": "MEDIUM", "value": "Medium", "label": "Medium Priority", "aliases": ["P2", "medium", "normal"], "sort_order": 3, "metadata": {"color": "#FFA500", "weight": 50}},
        {"code": "LOW", "value": "Low", "label": "Low Priority", "aliases": ["P3", "low", "minor"], "sort_order": 4, "metadata": {"color": "#32CD32", "weight": 25}},
        {"code": "NONE", "value": "None", "label": "No Priority", "aliases": ["P4", "none", "trivial"], "sort_order": 5, "metadata": {"color": "#808080", "weight": 0}},
    ]
}


# =============================================================================
# DEPARTMENT - Hierarchical terms (parent-child)
# =============================================================================
DEPARTMENT = {
    "code": "DEPARTMENT",
    "name": "Departments",
    "description": "Company organizational departments with hierarchy",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": True,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        # Top-level departments
        {"code": "ENG", "value": "Engineering", "label": "Engineering Department", "aliases": ["engineering", "tech"], "sort_order": 1},
        {"code": "SALES", "value": "Sales", "label": "Sales Department", "aliases": ["sales"], "sort_order": 2},
        {"code": "HR", "value": "Human Resources", "label": "Human Resources Department", "aliases": ["hr", "human resources", "people"], "sort_order": 3},
        {"code": "FIN", "value": "Finance", "label": "Finance Department", "aliases": ["finance", "accounting"], "sort_order": 4},
        {"code": "MKT", "value": "Marketing", "label": "Marketing Department", "aliases": ["marketing"], "sort_order": 5},
        {"code": "OPS", "value": "Operations", "label": "Operations Department", "aliases": ["operations", "ops"], "sort_order": 6},
        {"code": "LEGAL", "value": "Legal", "label": "Legal Department", "aliases": ["legal"], "sort_order": 7},
        # Engineering sub-departments (parent: ENG)
        {"code": "ENG_BE", "value": "Backend Engineering", "label": "Backend Engineering", "aliases": ["backend"], "sort_order": 8, "parent_code": "ENG"},
        {"code": "ENG_FE", "value": "Frontend Engineering", "label": "Frontend Engineering", "aliases": ["frontend"], "sort_order": 9, "parent_code": "ENG"},
        {"code": "ENG_QA", "value": "Quality Assurance", "label": "Quality Assurance", "aliases": ["qa", "testing"], "sort_order": 10, "parent_code": "ENG"},
        {"code": "ENG_DEVOPS", "value": "DevOps", "label": "DevOps Engineering", "aliases": ["devops", "infrastructure"], "sort_order": 11, "parent_code": "ENG"},
        {"code": "ENG_DATA", "value": "Data Engineering", "label": "Data Engineering", "aliases": ["data", "analytics"], "sort_order": 12, "parent_code": "ENG"},
        # Sales sub-departments (parent: SALES)
        {"code": "SALES_NA", "value": "North America Sales", "label": "North America Sales", "sort_order": 13, "parent_code": "SALES"},
        {"code": "SALES_EMEA", "value": "EMEA Sales", "label": "EMEA Sales", "sort_order": 14, "parent_code": "SALES"},
        {"code": "SALES_APAC", "value": "APAC Sales", "label": "APAC Sales", "sort_order": 15, "parent_code": "SALES"},
    ]
}


# =============================================================================
# PRODUCT_CATEGORY - E-commerce use case
# =============================================================================
PRODUCT_CATEGORY = {
    "code": "PRODUCT_CATEGORY",
    "name": "Product Categories",
    "description": "E-commerce product categories",
    "case_sensitive": False,
    "allow_multiple": True,
    "extensible": True,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "ELEC", "value": "Electronics", "label": "Electronics", "aliases": ["electronics", "tech"], "sort_order": 1},
        {"code": "CLOTH", "value": "Clothing", "label": "Clothing & Apparel", "aliases": ["clothing", "apparel", "fashion"], "sort_order": 2},
        {"code": "HOME", "value": "Home & Garden", "label": "Home & Garden", "aliases": ["home", "garden", "furniture"], "sort_order": 3},
        {"code": "SPORT", "value": "Sports & Outdoors", "label": "Sports & Outdoors", "aliases": ["sports", "outdoors", "fitness"], "sort_order": 4},
        {"code": "BOOK", "value": "Books", "label": "Books & Media", "aliases": ["books", "media", "reading"], "sort_order": 5},
        {"code": "BEAUTY", "value": "Beauty", "label": "Beauty & Personal Care", "aliases": ["beauty", "cosmetics", "personal care"], "sort_order": 6},
        {"code": "FOOD", "value": "Food & Grocery", "label": "Food & Grocery", "aliases": ["food", "grocery", "groceries"], "sort_order": 7},
        {"code": "TOYS", "value": "Toys & Games", "label": "Toys & Games", "aliases": ["toys", "games"], "sort_order": 8},
        {"code": "AUTO", "value": "Automotive", "label": "Automotive", "aliases": ["auto", "automotive", "car"], "sort_order": 9},
        {"code": "HEALTH", "value": "Health", "label": "Health & Wellness", "aliases": ["health", "wellness", "medical"], "sort_order": 10},
        {"code": "OFFICE", "value": "Office", "label": "Office Supplies", "aliases": ["office", "supplies", "stationery"], "sort_order": 11},
        {"code": "PET", "value": "Pet Supplies", "label": "Pet Supplies", "aliases": ["pets", "pet"], "sort_order": 12},
        {"code": "BABY", "value": "Baby", "label": "Baby Products", "aliases": ["baby", "infant", "kids"], "sort_order": 13},
        {"code": "JEWELRY", "value": "Jewelry", "label": "Jewelry & Watches", "aliases": ["jewelry", "watches", "accessories"], "sort_order": 14},
        {"code": "MUSIC", "value": "Musical Instruments", "label": "Musical Instruments", "aliases": ["music", "instruments"], "sort_order": 15},
        {"code": "SOFTWARE", "value": "Software", "label": "Software & Downloads", "aliases": ["software", "digital", "downloads"], "sort_order": 16},
        {"code": "CRAFT", "value": "Arts & Crafts", "label": "Arts & Crafts", "aliases": ["arts", "crafts", "diy"], "sort_order": 17},
        {"code": "TOOLS", "value": "Tools", "label": "Tools & Hardware", "aliases": ["tools", "hardware"], "sort_order": 18},
        {"code": "KITCHEN", "value": "Kitchen", "label": "Kitchen & Dining", "aliases": ["kitchen", "dining", "cookware"], "sort_order": 19},
        {"code": "TRAVEL", "value": "Travel", "label": "Travel & Luggage", "aliases": ["travel", "luggage", "bags"], "sort_order": 20},
    ]
}


# =============================================================================
# PAYMENT_METHOD - Transaction testing
# =============================================================================
PAYMENT_METHOD = {
    "code": "PAYMENT_METHOD",
    "name": "Payment Methods",
    "description": "Supported payment methods",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "CARD", "value": "Credit Card", "label": "Credit/Debit Card", "aliases": ["card", "credit", "debit", "credit card", "debit card"], "sort_order": 1, "metadata": {"icon": "credit-card"}},
        {"code": "BANK", "value": "Bank Transfer", "label": "Bank Transfer", "aliases": ["bank", "wire", "transfer", "ach", "sepa"], "sort_order": 2, "metadata": {"icon": "bank"}},
        {"code": "PAYPAL", "value": "PayPal", "label": "PayPal", "aliases": ["paypal", "pp"], "sort_order": 3, "metadata": {"icon": "paypal"}},
        {"code": "APPLE", "value": "Apple Pay", "label": "Apple Pay", "aliases": ["apple pay", "applepay"], "sort_order": 4, "metadata": {"icon": "apple"}},
        {"code": "GOOGLE", "value": "Google Pay", "label": "Google Pay", "aliases": ["google pay", "googlepay", "gpay"], "sort_order": 5, "metadata": {"icon": "google"}},
        {"code": "CRYPTO", "value": "Cryptocurrency", "label": "Cryptocurrency", "aliases": ["crypto", "bitcoin", "btc", "eth"], "sort_order": 6, "metadata": {"icon": "bitcoin"}},
        {"code": "CASH", "value": "Cash", "label": "Cash", "aliases": ["cash", "cod"], "sort_order": 7, "metadata": {"icon": "money"}},
        {"code": "CHECK", "value": "Check", "label": "Check/Cheque", "aliases": ["check", "cheque"], "sort_order": 8, "metadata": {"icon": "file-text"}},
    ]
}


# =============================================================================
# EMPLOYMENT_TYPE - HR use case
# =============================================================================
EMPLOYMENT_TYPE = {
    "code": "EMPLOYMENT_TYPE",
    "name": "Employment Types",
    "description": "Types of employment relationships",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "FT", "value": "Full-time", "label": "Full-time Employee", "aliases": ["full-time", "fulltime", "permanent"], "sort_order": 1},
        {"code": "PT", "value": "Part-time", "label": "Part-time Employee", "aliases": ["part-time", "parttime"], "sort_order": 2},
        {"code": "CONT", "value": "Contractor", "label": "Contractor", "aliases": ["contractor", "freelance", "freelancer"], "sort_order": 3},
        {"code": "TEMP", "value": "Temporary", "label": "Temporary Employee", "aliases": ["temp", "temporary"], "sort_order": 4},
        {"code": "INTERN", "value": "Intern", "label": "Intern", "aliases": ["intern", "internship"], "sort_order": 5},
        {"code": "CONSULT", "value": "Consultant", "label": "Consultant", "aliases": ["consultant", "consulting"], "sort_order": 6},
    ]
}


# =============================================================================
# MARITAL_STATUS - Personal data
# =============================================================================
MARITAL_STATUS = {
    "code": "MARITAL_STATUS",
    "name": "Marital Status",
    "description": "Marital status options",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "SINGLE", "value": "Single", "label": "Single", "aliases": ["single", "unmarried"], "sort_order": 1},
        {"code": "MARRIED", "value": "Married", "label": "Married", "aliases": ["married"], "sort_order": 2},
        {"code": "DIVORCED", "value": "Divorced", "label": "Divorced", "aliases": ["divorced"], "sort_order": 3},
        {"code": "WIDOWED", "value": "Widowed", "label": "Widowed", "aliases": ["widowed", "widow", "widower"], "sort_order": 4},
        {"code": "PARTNER", "value": "Domestic Partnership", "label": "Domestic Partnership", "aliases": ["partner", "civil union", "domestic partner"], "sort_order": 5},
    ]
}


# =============================================================================
# BLOOD_TYPE - Healthcare use case
# =============================================================================
BLOOD_TYPE = {
    "code": "BLOOD_TYPE",
    "name": "Blood Types",
    "description": "ABO blood group types with Rh factor",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "medical",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "A_POS", "value": "A+", "label": "A Positive", "aliases": ["A+", "A positive", "a+"], "sort_order": 1, "metadata": {"can_donate_to": ["A+", "AB+"], "can_receive_from": ["A+", "A-", "O+", "O-"]}},
        {"code": "A_NEG", "value": "A-", "label": "A Negative", "aliases": ["A-", "A negative", "a-"], "sort_order": 2, "metadata": {"can_donate_to": ["A+", "A-", "AB+", "AB-"], "can_receive_from": ["A-", "O-"]}},
        {"code": "B_POS", "value": "B+", "label": "B Positive", "aliases": ["B+", "B positive", "b+"], "sort_order": 3, "metadata": {"can_donate_to": ["B+", "AB+"], "can_receive_from": ["B+", "B-", "O+", "O-"]}},
        {"code": "B_NEG", "value": "B-", "label": "B Negative", "aliases": ["B-", "B negative", "b-"], "sort_order": 4, "metadata": {"can_donate_to": ["B+", "B-", "AB+", "AB-"], "can_receive_from": ["B-", "O-"]}},
        {"code": "AB_POS", "value": "AB+", "label": "AB Positive", "aliases": ["AB+", "AB positive", "ab+"], "sort_order": 5, "metadata": {"can_donate_to": ["AB+"], "can_receive_from": ["all"]}},
        {"code": "AB_NEG", "value": "AB-", "label": "AB Negative", "aliases": ["AB-", "AB negative", "ab-"], "sort_order": 6, "metadata": {"can_donate_to": ["AB+", "AB-"], "can_receive_from": ["A-", "B-", "AB-", "O-"]}},
        {"code": "O_POS", "value": "O+", "label": "O Positive", "aliases": ["O+", "O positive", "o+"], "sort_order": 7, "metadata": {"can_donate_to": ["A+", "B+", "AB+", "O+"], "can_receive_from": ["O+", "O-"]}},
        {"code": "O_NEG", "value": "O-", "label": "O Negative", "aliases": ["O-", "O negative", "o-"], "sort_order": 8, "metadata": {"can_donate_to": ["all"], "can_receive_from": ["O-"]}},
    ]
}


# =============================================================================
# UNIT_OF_MEASURE - Inventory testing
# =============================================================================
UNIT_OF_MEASURE = {
    "code": "UNIT_OF_MEASURE",
    "name": "Units of Measure",
    "description": "Standard units of measurement",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": True,
    "metadata": {
        "source": "SI/Imperial",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        # Count
        {"code": "EACH", "value": "Each", "label": "Each", "aliases": ["each", "ea", "unit", "pc", "pcs", "piece", "pieces"], "sort_order": 1, "metadata": {"category": "count"}},
        {"code": "DOZEN", "value": "Dozen", "label": "Dozen", "aliases": ["dozen", "dz", "doz"], "sort_order": 2, "metadata": {"category": "count", "conversion": {"EACH": 12}}},
        {"code": "GROSS", "value": "Gross", "label": "Gross (144)", "aliases": ["gross", "gr"], "sort_order": 3, "metadata": {"category": "count", "conversion": {"EACH": 144}}},
        # Weight
        {"code": "KG", "value": "Kilogram", "label": "Kilogram", "aliases": ["kg", "kilo", "kilos", "kilogram", "kilograms"], "sort_order": 4, "metadata": {"category": "weight", "si": True}},
        {"code": "G", "value": "Gram", "label": "Gram", "aliases": ["g", "gram", "grams"], "sort_order": 5, "metadata": {"category": "weight", "si": True, "conversion": {"KG": 0.001}}},
        {"code": "MG", "value": "Milligram", "label": "Milligram", "aliases": ["mg", "milligram", "milligrams"], "sort_order": 6, "metadata": {"category": "weight", "si": True, "conversion": {"G": 0.001}}},
        {"code": "LB", "value": "Pound", "label": "Pound", "aliases": ["lb", "lbs", "pound", "pounds"], "sort_order": 7, "metadata": {"category": "weight", "conversion": {"KG": 0.453592}}},
        {"code": "OZ", "value": "Ounce", "label": "Ounce", "aliases": ["oz", "ounce", "ounces"], "sort_order": 8, "metadata": {"category": "weight", "conversion": {"LB": 0.0625}}},
        # Length
        {"code": "M", "value": "Meter", "label": "Meter", "aliases": ["m", "meter", "meters", "metre", "metres"], "sort_order": 9, "metadata": {"category": "length", "si": True}},
        {"code": "CM", "value": "Centimeter", "label": "Centimeter", "aliases": ["cm", "centimeter", "centimeters"], "sort_order": 10, "metadata": {"category": "length", "si": True, "conversion": {"M": 0.01}}},
        {"code": "MM", "value": "Millimeter", "label": "Millimeter", "aliases": ["mm", "millimeter", "millimeters"], "sort_order": 11, "metadata": {"category": "length", "si": True, "conversion": {"CM": 0.1}}},
        {"code": "IN", "value": "Inch", "label": "Inch", "aliases": ["in", "inch", "inches", "\""], "sort_order": 12, "metadata": {"category": "length", "conversion": {"CM": 2.54}}},
        {"code": "FT", "value": "Foot", "label": "Foot", "aliases": ["ft", "foot", "feet", "'"], "sort_order": 13, "metadata": {"category": "length", "conversion": {"IN": 12}}},
        {"code": "YD", "value": "Yard", "label": "Yard", "aliases": ["yd", "yard", "yards"], "sort_order": 14, "metadata": {"category": "length", "conversion": {"FT": 3}}},
        # Volume
        {"code": "L", "value": "Liter", "label": "Liter", "aliases": ["l", "liter", "liters", "litre", "litres"], "sort_order": 15, "metadata": {"category": "volume", "si": True}},
        {"code": "ML", "value": "Milliliter", "label": "Milliliter", "aliases": ["ml", "milliliter", "milliliters"], "sort_order": 16, "metadata": {"category": "volume", "si": True, "conversion": {"L": 0.001}}},
        {"code": "GAL", "value": "Gallon", "label": "Gallon (US)", "aliases": ["gal", "gallon", "gallons"], "sort_order": 17, "metadata": {"category": "volume", "conversion": {"L": 3.78541}}},
        {"code": "QT", "value": "Quart", "label": "Quart", "aliases": ["qt", "quart", "quarts"], "sort_order": 18, "metadata": {"category": "volume", "conversion": {"GAL": 0.25}}},
        {"code": "PT", "value": "Pint", "label": "Pint", "aliases": ["pt", "pint", "pints"], "sort_order": 19, "metadata": {"category": "volume", "conversion": {"QT": 0.5}}},
        {"code": "FLOZ", "value": "Fluid Ounce", "label": "Fluid Ounce", "aliases": ["fl oz", "fluid ounce", "floz"], "sort_order": 20, "metadata": {"category": "volume", "conversion": {"PT": 0.0625}}},
        # Area
        {"code": "SQM", "value": "Square Meter", "label": "Square Meter", "aliases": ["sqm", "m2", "sq m"], "sort_order": 21, "metadata": {"category": "area", "si": True}},
        {"code": "SQFT", "value": "Square Foot", "label": "Square Foot", "aliases": ["sqft", "ft2", "sq ft"], "sort_order": 22, "metadata": {"category": "area", "conversion": {"SQM": 0.092903}}},
        # Time
        {"code": "HR", "value": "Hour", "label": "Hour", "aliases": ["hr", "hour", "hours", "h"], "sort_order": 23, "metadata": {"category": "time"}},
        {"code": "DAY", "value": "Day", "label": "Day", "aliases": ["day", "days", "d"], "sort_order": 24, "metadata": {"category": "time", "conversion": {"HR": 24}}},
        {"code": "WEEK", "value": "Week", "label": "Week", "aliases": ["week", "weeks", "wk"], "sort_order": 25, "metadata": {"category": "time", "conversion": {"DAY": 7}}},
    ]
}


# =============================================================================
# SEVERITY - Issue tracking
# =============================================================================
SEVERITY = {
    "code": "SEVERITY",
    "name": "Severity Levels",
    "description": "Issue and bug severity levels",
    "case_sensitive": False,
    "allow_multiple": False,
    "extensible": False,
    "metadata": {
        "source": "internal",
        "version": "1.0",
        "language": "en"
    },
    "terms": [
        {"code": "S1", "value": "Critical", "label": "Severity 1 - Critical", "aliases": ["critical", "s1", "sev1"], "sort_order": 1, "metadata": {"color": "#DC143C", "response_time_hours": 1}},
        {"code": "S2", "value": "Major", "label": "Severity 2 - Major", "aliases": ["major", "s2", "sev2"], "sort_order": 2, "metadata": {"color": "#FF4500", "response_time_hours": 4}},
        {"code": "S3", "value": "Moderate", "label": "Severity 3 - Moderate", "aliases": ["moderate", "s3", "sev3"], "sort_order": 3, "metadata": {"color": "#FFA500", "response_time_hours": 24}},
        {"code": "S4", "value": "Minor", "label": "Severity 4 - Minor", "aliases": ["minor", "s4", "sev4"], "sort_order": 4, "metadata": {"color": "#32CD32", "response_time_hours": 72}},
        {"code": "S5", "value": "Cosmetic", "label": "Severity 5 - Cosmetic", "aliases": ["cosmetic", "s5", "sev5", "trivial"], "sort_order": 5, "metadata": {"color": "#808080", "response_time_hours": 168}},
    ]
}
