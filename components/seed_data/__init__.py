"""
Comprehensive seed data module for World In a Pie (WIP).

This module provides test data generation for all WIP services:
- Terminologies and Terms (Def-Store)
- Templates (Template Store)
- Documents (Document Store)

Usage:
    from seed_data import terminologies, templates, documents, generators
"""

__version__ = "1.0.0"

from . import terminologies
from . import templates
from . import documents
from . import generators
from . import performance
