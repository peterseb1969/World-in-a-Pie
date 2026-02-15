#!/usr/bin/env python3
"""
Import real-world reference datasets from testdata/ into the Def-Store as terminologies.

Each dataset is transformed into the Def-Store JSON import format and loaded via
POST /api/def-store/import-export/import.

Usage:
    # Import all datasets
    python scripts/import_testdata.py --all

    # Import specific datasets
    python scripts/import_testdata.py --dataset ISO_4217_CURRENCY ISO_639_LANGUAGE

    # Dry-run (transform only, no API call)
    python scripts/import_testdata.py --all --dry-run

    # Custom API target
    python scripts/import_testdata.py --all --api-url http://localhost:8002 --api-key dev_master_key_for_testing

    # Custom namespace and batch sizes
    python scripts/import_testdata.py --dataset FDA_PRODUCT --namespace testdata --registry-batch-size 50
"""

import argparse
import asyncio
import csv
import io
import json
import sys
import zipfile
from collections import OrderedDict
from pathlib import Path

import httpx

# Resolve testdata/ relative to this script's location
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
TESTDATA_DIR = PROJECT_ROOT / "testdata"

DEFAULT_API_URL = "http://localhost:8002"
DEFAULT_API_KEY = "dev_master_key_for_testing"


# =============================================================================
# TRANSFORMERS
# =============================================================================

def transform_iso4217(path: Path, namespace: str) -> dict:
    """ISO 4217 Currency Codes from filtered_data.csv."""
    currencies = OrderedDict()  # AlphabeticCode -> {term_data, entities: []}

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row.get("AlphabeticCode", "").strip()
            entity = row.get("Entity", "").strip()
            withdrawal = row.get("WithdrawalDate", "").strip()

            # Skip rows without a code or with withdrawal dates
            if not code or withdrawal:
                continue

            if code in currencies:
                # Append entity to existing currency
                currencies[code]["entities"].append(entity)
            else:
                currencies[code] = {
                    "currency": row.get("Currency", "").strip(),
                    "numeric_code": row.get("NumericCode", "").strip(),
                    "minor_unit": row.get("MinorUnit", "").strip(),
                    "entities": [entity],
                }

    terms = []
    for i, (code, info) in enumerate(sorted(currencies.items())):
        terms.append({
            "value": code,
            "label": info["currency"],
            "sort_order": i,
            "metadata": {
                "numeric_code": info["numeric_code"],
                "minor_unit": info["minor_unit"],
                "entities": info["entities"],
            },
        })

    return {
        "terminology": {
            "value": "ISO_4217_CURRENCY",
            "label": "ISO 4217 Currency Codes",
            "description": "Active ISO 4217 currency codes with associated countries/entities.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "ISO 4217",
                "source_url": "https://www.iso.org/iso-4217-currency-codes.html",
                "version": "2024",
                "language": "en",
            },
        },
        "terms": terms,
    }


def transform_iso639(path: Path, namespace: str) -> dict:
    """ISO 639-1 Language Codes from iso_639-1.csv."""
    terms = []

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            code = row.get("639-1", "").strip()
            name = row.get("name", "").strip()
            native = row.get("nativeName", "").strip()

            if not code or not name:
                continue

            aliases = []
            if native and native != name:
                # Native name may have comma-separated variants
                for part in native.split(","):
                    part = part.strip()
                    if part and part != name:
                        aliases.append(part)

            term = {
                "value": code,
                "label": name,
                "sort_order": i,
                "metadata": {
                    "family": row.get("family", "").strip(),
                    "native_name": native,
                    "iso_639_2": row.get("639-2", "").strip(),
                },
            }
            if aliases:
                term["aliases"] = aliases

            iso_639_2b = row.get("639-2/B", "").strip()
            if iso_639_2b:
                term["metadata"]["iso_639_2b"] = iso_639_2b

            terms.append(term)

    return {
        "terminology": {
            "value": "ISO_639_LANGUAGE",
            "label": "ISO 639-1 Language Codes",
            "description": "ISO 639-1 two-letter language codes with native names and language families.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "ISO 639-1",
                "source_url": "https://www.iso.org/iso-639-language-code",
                "version": "2024",
                "language": "en",
            },
        },
        "terms": terms,
    }


def transform_iso3166_de(path: Path, namespace: str) -> dict:
    """ISO 3166 Country Codes (German) from german-iso-3166.csv."""
    terms = []

    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            code = parts[0].strip()
            label = parts[1].strip()

            if not code or not label:
                continue

            terms.append({
                "value": code,
                "label": label,
                "sort_order": i,
                "metadata": {"language": "de"},
            })

    return {
        "terminology": {
            "value": "ISO_3166_COUNTRY_DE",
            "label": "ISO 3166 Ländercodes (Deutsch)",
            "description": "ISO 3166-1 Alpha-2 Ländercodes mit deutschen Bezeichnungen.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "ISO 3166",
                "source_url": "https://www.iso.org/iso-3166-country-codes.html",
                "version": "2024",
                "language": "de",
            },
        },
        "terms": terms,
    }


def transform_naics(path: Path, namespace: str) -> dict:
    """NAICS 2022 Industry Codes from naics-2022-v1.0-isic4-en.csv."""
    # First pass: collect all ISIC mappings per NAICS code
    naics_data = OrderedDict()  # code -> {title, isic_mappings: [], notes}

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Handle BOM in column name
            code = (row.get("\ufeffNAICS Canada 2022 Version 1.0 Code")
                    or row.get("NAICS Canada 2022 Version 1.0 Code", "")).strip()
            title = (row.get("\ufeffNAICS Canada 2022 Version 1.0 Title")
                     or row.get("NAICS Canada 2022 Version 1.0 Title", "")).strip()
            isic_code = row.get("ISIC Rev. 4 Code", "").strip()
            isic_title = row.get("ISIC Rev. 4 Title", "").strip()
            notes = row.get("Explanatory Notes", "").strip()

            if not code:
                continue

            if code in naics_data:
                # Additional ISIC mapping for same NAICS code
                if isic_code:
                    naics_data[code]["isic_mappings"].append({
                        "code": isic_code,
                        "title": isic_title,
                    })
            else:
                naics_data[code] = {
                    "title": title,
                    "isic_mappings": [{"code": isic_code, "title": isic_title}] if isic_code else [],
                    "notes": notes,
                }

    # Build parent lookup: derive parent from code prefix
    all_codes = set(naics_data.keys())

    def find_parent(code: str) -> str | None:
        """Find the parent code by progressively shortening."""
        for length in range(len(code) - 1, 1, -1):
            candidate = code[:length]
            if candidate in all_codes:
                return candidate
        return None

    terms = []
    for i, (code, info) in enumerate(naics_data.items()):
        level = len(code)
        sector = code[:2] if len(code) >= 2 else code

        metadata = {
            "level": level,
            "sector": sector,
        }
        if info["isic_mappings"]:
            metadata["isic_mappings"] = info["isic_mappings"]

        parent = find_parent(code)
        if parent:
            metadata["parent_code"] = parent

        term = {
            "value": code,
            "label": info["title"],
            "sort_order": i,
            "metadata": metadata,
        }
        if info["notes"]:
            term["description"] = info["notes"]

        terms.append(term)

    return {
        "terminology": {
            "value": "NAICS_2022",
            "label": "NAICS 2022 Industry Codes",
            "description": "North American Industry Classification System (NAICS) 2022 codes with ISIC Rev. 4 cross-references.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "Statistics Canada / US Census Bureau",
                "version": "2022 v1.0",
                "language": "en",
            },
        },
        "terms": terms,
    }


def transform_fda_product(path: Path, namespace: str) -> dict:
    """FDA Product database from product.csv."""
    terms = []

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            code = (row.get("\ufeffcode") or row.get("code", "")).strip()
            product_type = row.get("value", "").strip()
            label = row.get("label", "").strip()
            description = row.get("description", "").strip()

            if not code or not label:
                continue

            term = {
                "value": code,
                "label": label,
                "sort_order": i,
                "metadata": {},
            }
            if product_type:
                term["metadata"]["product_type"] = product_type
            if description:
                term["metadata"]["pharmacological_class"] = description

            terms.append(term)

    return {
        "terminology": {
            "value": "FDA_PRODUCT",
            "label": "FDA Product Database",
            "description": "US FDA registered pharmaceutical products with NDC codes and pharmacological classifications.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "US FDA National Drug Code Directory",
                "source_url": "https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory",
                "version": "2024",
                "language": "en",
            },
        },
        "terms": terms,
    }


def transform_icd10(path: Path, namespace: str) -> dict:
    """ICD-10-GM 2025 from icd10gm2025syst-meta.zip."""
    with zipfile.ZipFile(path) as zf:
        # Read chapter names for terminology metadata
        chapters = {}
        with zf.open("Klassifikationsdateien/icd10gm2025syst_kapitel.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(";")
                if len(parts) >= 2:
                    chapters[parts[0].strip()] = parts[1].strip()

        # Read terminal codes
        terms = []
        with zf.open("Klassifikationsdateien/icd10gm2025syst_kodes.txt") as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                fields = line.split(";")
                if len(fields) < 11:
                    continue

                # field[1]: T = Terminal, N = Non-terminal
                if fields[1] != "T":
                    continue

                level = fields[0].strip()
                chapter = fields[3].strip()
                code_with_dot = fields[6].strip()
                code_no_dot = fields[7].strip()
                desc_short = fields[8].strip()
                desc_category = fields[9].strip()
                desc_long = fields[10].strip() if len(fields) > 10 else ""

                label = desc_long if desc_long else desc_short

                terms.append({
                    "value": code_with_dot,
                    "label": label,
                    "sort_order": len(terms),
                    "metadata": {
                        "chapter": chapter,
                        "chapter_name": chapters.get(chapter, ""),
                        "category": desc_category if desc_category != label else "",
                        "code_nodot": code_no_dot,
                        "level": level,
                    },
                })

    return {
        "terminology": {
            "value": "ICD10_GM_2025",
            "label": "ICD-10-GM 2025",
            "description": "Internationale statistische Klassifikation der Krankheiten und verwandter Gesundheitsprobleme, 10. Revision, German Modification, Version 2025.",
            "case_sensitive": False,
            "extensible": False,
            "namespace": namespace,
            "metadata": {
                "source": "BfArM (Bundesinstitut für Arzneimittel und Medizinprodukte)",
                "source_url": "https://www.bfarm.de/DE/Kodiersysteme/Klassifikationen/ICD/ICD-10-GM/_node.html",
                "version": "2025",
                "language": "de",
                "chapters": chapters,
            },
        },
        "terms": terms,
    }


# =============================================================================
# DATASET REGISTRY
# =============================================================================

DATASETS = {
    "ISO_4217_CURRENCY": {
        "file": "filtered_data.csv",
        "transformer": transform_iso4217,
        "description": "ISO 4217 active currency codes (~170 unique)",
    },
    "ISO_639_LANGUAGE": {
        "file": "iso_639-1.csv",
        "transformer": transform_iso639,
        "description": "ISO 639-1 two-letter language codes (184 languages)",
    },
    "ISO_3166_COUNTRY_DE": {
        "file": "german-iso-3166.csv",
        "transformer": transform_iso3166_de,
        "description": "ISO 3166-1 country codes with German names (237 countries)",
    },
    "NAICS_2022": {
        "file": "naics-2022-v1.0-isic4-en.csv",
        "transformer": transform_naics,
        "description": "NAICS 2022 industry classification codes (~1,060 unique)",
    },
    "FDA_PRODUCT": {
        "file": "product.csv",
        "transformer": transform_fda_product,
        "description": "FDA pharmaceutical product database (~110k products)",
    },
    "ICD10_GM_2025": {
        "file": "icd10gm2025syst-meta.zip",
        "transformer": transform_icd10,
        "description": "ICD-10-GM 2025 German medical classification (~14,300 terminal codes)",
    },
}


# =============================================================================
# IMPORT HELPER
# =============================================================================

async def import_terminology(
    api_url: str,
    api_key: str,
    data: dict,
    batch_size: int,
    registry_batch_size: int,
) -> dict | None:
    """Post terminology data to the Def-Store import API."""
    term_count = len(data["terms"])
    # Scale timeout: 60s base + 1s per 100 terms
    timeout = max(60.0, 60.0 + term_count / 100)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{api_url}/api/def-store/import-export/import",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
            },
            params={
                "format": "json",
                "batch_size": batch_size,
                "registry_batch_size": registry_batch_size,
            },
            json=data,
        )

        if response.status_code != 200:
            print(f"  ERROR: {response.status_code} - {response.text[:500]}")
            return None

        return response.json()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Import real-world reference datasets from testdata/ into the Def-Store."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Import all datasets")
    group.add_argument(
        "--dataset", nargs="+", choices=list(DATASETS.keys()),
        help="Import specific datasets",
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help=f"Def-Store API URL (default: {DEFAULT_API_URL})")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key")
    parser.add_argument("--namespace", default="wip", help="Namespace for terminologies (default: wip)")
    parser.add_argument("--dry-run", action="store_true", help="Transform only, do not call API")
    parser.add_argument("--batch-size", type=int, default=1000, help="MongoDB batch size (default: 1000)")
    parser.add_argument("--registry-batch-size", type=int, default=100, help="Registry HTTP batch size (default: 100)")
    args = parser.parse_args()

    datasets_to_import = list(DATASETS.keys()) if args.all else args.dataset

    print("=" * 70)
    print("WIP Testdata Importer")
    print("=" * 70)
    if args.dry_run:
        print("MODE: Dry-run (no API calls)")
    else:
        print(f"API:  {args.api_url}")
    print(f"Namespace: {args.namespace}")
    print(f"Datasets:  {', '.join(datasets_to_import)}")
    print("=" * 70)

    results = {}
    for name in datasets_to_import:
        ds = DATASETS[name]
        file_path = TESTDATA_DIR / ds["file"]

        print(f"\n--- {name} ---")
        print(f"  Source: {ds['file']}")
        print(f"  {ds['description']}")

        if not file_path.exists():
            print(f"  SKIP: File not found: {file_path}")
            results[name] = "file_not_found"
            continue

        # Transform
        print("  Transforming...", end=" ", flush=True)
        try:
            data = ds["transformer"](file_path, args.namespace)
        except Exception as e:
            print(f"FAILED: {e}")
            results[name] = "transform_error"
            continue

        term_count = len(data["terms"])
        print(f"{term_count} terms")

        if args.dry_run:
            # Show sample terms
            sample = data["terms"][:3]
            for t in sample:
                print(f"    {t['value']}: {t['label']}")
            if term_count > 3:
                print(f"    ... and {term_count - 3} more")
            results[name] = f"dry_run:{term_count}"
            continue

        # Import
        print(f"  Importing (batch_size={args.batch_size}, registry_batch_size={args.registry_batch_size})...")
        result = await import_terminology(
            api_url=args.api_url,
            api_key=args.api_key,
            data=data,
            batch_size=args.batch_size,
            registry_batch_size=args.registry_batch_size,
        )

        if result:
            t_status = result.get("terminology", {}).get("status", "?")
            t_id = result.get("terminology", {}).get("id", "?")
            terms_info = result.get("terms", {})
            created = terms_info.get("created", 0)
            skipped = terms_info.get("skipped", 0)
            errors = terms_info.get("errors", 0)
            print(f"  Terminology: {t_status} ({t_id})")
            print(f"  Terms: {created} created, {skipped} skipped, {errors} errors")
            results[name] = f"ok:{created}+{skipped}"
        else:
            results[name] = "api_error"

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, status in results.items():
        print(f"  {name:30s} {status}")
    print("=" * 70)

    # Return non-zero if any failures
    failures = [s for s in results.values() if s.startswith(("api_error", "transform_error", "file_not_found"))]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
