"""
Study Plan Use Case

Demonstrates using WIP for clinical study planning with:
- Per-study terminologies for arms and timepoints
- Template inheritance for specialized event types
- Relative time modeling (days from baseline)

Contents:
- terminologies.py: Global and per-study terminology definitions
- templates.py: Template definitions for study components
- demo_data.py: DEMO-001 sample study plan
- seed.py: Script to load everything into WIP

Usage:
    cd docs/use-cases/study-plan
    pip install -r requirements.txt
    python seed.py --base-url http://localhost
"""
