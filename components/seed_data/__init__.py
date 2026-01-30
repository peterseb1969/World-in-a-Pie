"""
Comprehensive seed data module for World In a Pie (WIP).

This module provides test data generation for all WIP services:
- Terminologies and Terms (Def-Store)
- Templates (Template Store)
- Documents (Document Store)

The document generation is template-driven: generators read template definitions
and terminology values to automatically produce valid documents that satisfy
all validation rules.

Usage:
    from seed_data import terminologies, templates, documents, generators

    # Generate a document for any template
    person = generators.generate_document("PERSON", index=0)

    # Get all terminology definitions
    terms = terminologies.get_terminology_definitions()

    # Get all template definitions
    tmpls = templates.get_template_definitions()
"""

__version__ = "1.0.0"

from . import terminologies
from . import templates
from . import documents
from . import generators
from . import document_generator
from . import performance
