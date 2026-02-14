#!/usr/bin/env python3
"""
Import ISO 3166 Country Codes into the Def-Store.

Downloads country data from GitHub and imports it as a terminology.

Usage:
    python import_iso3166.py [--api-url URL] [--api-key KEY]
"""

import argparse
import asyncio
import httpx

# Data source
ISO3166_URL = "https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-Regional-Codes/master/all/all.json"

# Default API settings
DEFAULT_API_URL = "http://localhost:8002"
DEFAULT_API_KEY = "dev_master_key_for_testing"


async def fetch_countries() -> list[dict]:
    """Fetch ISO 3166 country data from GitHub."""
    print(f"Fetching country data from {ISO3166_URL}...")
    async with httpx.AsyncClient() as client:
        response = await client.get(ISO3166_URL)
        response.raise_for_status()
        countries = response.json()
        print(f"  Found {len(countries)} countries/territories")
        return countries


def transform_to_terminology(countries: list[dict]) -> dict:
    """Transform country data into Def-Store import format."""
    terms = []

    for i, country in enumerate(countries):
        # Use alpha-2 code as the primary code
        alpha2 = country.get("alpha-2", "")
        alpha3 = country.get("alpha-3", "")
        name = country.get("name", "")

        if not alpha2 or not name:
            continue

        term = {
            "value": alpha2.lower(),  # us, de, fr
            "label": name,  # United States of America
            "description": f"{name} ({alpha3})",
            "sort_order": i,
            "metadata": {
                "alpha2": alpha2,
                "alpha3": alpha3,
                "numeric": country.get("country-code", ""),
                "region": country.get("region", ""),
                "sub_region": country.get("sub-region", ""),
                "intermediate_region": country.get("intermediate-region", ""),
                "region_code": country.get("region-code", ""),
                "sub_region_code": country.get("sub-region-code", ""),
            }
        }
        terms.append(term)

    return {
        "terminology": {
            "value": "ISO_3166_COUNTRY",
            "label": "ISO 3166 Country Codes",
            "description": "ISO 3166-1 country codes including alpha-2, alpha-3, and numeric codes with regional classifications.",
            "case_sensitive": False,
            "allow_multiple": False,
            "extensible": False,
            "metadata": {
                "source": "ISO 3166",
                "source_url": "https://www.iso.org/iso-3166-country-codes.html",
                "version": "2024",
                "language": "en"
            }
        },
        "terms": terms
    }


async def import_terminology(api_url: str, api_key: str, data: dict) -> dict:
    """Import terminology into Def-Store."""
    print(f"\nImporting to {api_url}...")
    print(f"  Terminology: {data['terminology']['value']}")
    print(f"  Terms: {len(data['terms'])}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{api_url}/api/def-store/import-export/import",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json"
            },
            params={"format": "json"},
            json=data
        )

        if response.status_code != 200:
            print(f"  ERROR: {response.status_code} - {response.text}")
            return None

        result = response.json()
        print(f"  Terminology: {result['terminology']['status']}")
        print(f"  Terms created: {result['terms']['created']}")
        print(f"  Terms skipped: {result['terms']['skipped']}")
        if result['terms']['errors'] > 0:
            print(f"  Errors: {result['terms']['errors']}")

        return result


async def main():
    parser = argparse.ArgumentParser(description="Import ISO 3166 country codes")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Def-Store API URL")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key")
    args = parser.parse_args()

    print("=" * 60)
    print("ISO 3166 Country Codes Importer")
    print("=" * 60)

    # Fetch data
    countries = await fetch_countries()

    # Transform
    print("\nTransforming data...")
    terminology_data = transform_to_terminology(countries)
    print(f"  Prepared {len(terminology_data['terms'])} terms")

    # Import
    result = await import_terminology(args.api_url, args.api_key, terminology_data)

    if result:
        print("\n" + "=" * 60)
        print("Import complete!")
        print(f"Terminology ID: {result['terminology']['id']}")
        print("=" * 60)
    else:
        print("\nImport failed!")
        return 1

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
