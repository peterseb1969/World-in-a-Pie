#!/bin/bash
# Import ISO terminologies into the Def-Store
#
# Usage: ./import_iso_terminologies.sh [API_URL] [API_KEY]
#
# Requires: curl, python3

set -e

API_URL="${1:-http://localhost:8002}"
API_KEY="${2:-dev_master_key_for_testing}"

echo "=============================================="
echo "ISO Terminologies Importer"
echo "=============================================="
echo "API URL: $API_URL"
echo ""

# Function to import a terminology
import_terminology() {
    local name="$1"
    local json_file="$2"

    echo "Importing $name..."
    result=$(/usr/bin/curl -s -X POST "$API_URL/api/def-store/import-export/import?format=json" \
        --header "X-API-Key: $API_KEY" \
        --header "Content-Type: application/json" \
        --data-binary "@$json_file")

    status=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['terminology']['status'])")
    created=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['terms']['created'])")
    skipped=$(echo "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['terms']['skipped'])")

    echo "  Status: $status"
    echo "  Created: $created, Skipped: $skipped"
    echo ""
}

# ============================================
# ISO 3166 Country Codes
# ============================================
echo "Downloading ISO 3166 Country Codes..."
/usr/bin/curl -s "https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-Regional-Codes/master/all/all.json" -o /tmp/iso3166_raw.json

python3 << 'EOF' > /tmp/iso3166_import.json
import json
with open('/tmp/iso3166_raw.json', 'r') as f:
    countries = json.load(f)

terms = []
for i, country in enumerate(countries):
    alpha2 = country.get("alpha-2", "")
    alpha3 = country.get("alpha-3", "")
    name = country.get("name", "")
    if not alpha2 or not name:
        continue
    terms.append({
        "code": alpha2,
        "value": alpha2.lower(),
        "label": name,
        "description": f"{name} ({alpha3})",
        "sort_order": i,
        "metadata": {
            "alpha2": alpha2,
            "alpha3": alpha3,
            "numeric": country.get("country-code", ""),
            "region": country.get("region", ""),
            "sub_region": country.get("sub-region", ""),
        }
    })

print(json.dumps({
    "terminology": {
        "code": "ISO_3166_COUNTRY",
        "name": "ISO 3166 Country Codes",
        "description": "ISO 3166-1 country codes with regional classifications",
        "case_sensitive": False,
        "metadata": {
            "source": "ISO 3166",
            "source_url": "https://www.iso.org/iso-3166-country-codes.html",
            "version": "2024"
        }
    },
    "terms": terms
}))
EOF

import_terminology "ISO 3166 Country Codes" "/tmp/iso3166_import.json"

# ============================================
# ISO 639 Language Codes
# ============================================
echo "Downloading ISO 639 Language Codes..."
/usr/bin/curl -s "https://raw.githubusercontent.com/haliaeetus/iso-639/master/data/iso_639-1.json" -o /tmp/iso639_raw.json

python3 << 'EOF' > /tmp/iso639_import.json
import json
with open('/tmp/iso639_raw.json', 'r') as f:
    languages = json.load(f)

terms = []
for i, (code, lang) in enumerate(languages.items()):
    terms.append({
        "code": code.upper(),
        "value": code,
        "label": lang.get("name", code),
        "description": f"{lang.get('name', '')} - {lang.get('nativeName', '')}",
        "sort_order": i,
        "metadata": {
            "iso639_1": lang.get("639-1", ""),
            "iso639_2": lang.get("639-2", ""),
            "family": lang.get("family", ""),
            "native_name": lang.get("nativeName", ""),
        }
    })

print(json.dumps({
    "terminology": {
        "code": "ISO_639_LANGUAGE",
        "name": "ISO 639 Language Codes",
        "description": "ISO 639-1 language codes with native names and language families",
        "case_sensitive": False,
        "metadata": {
            "source": "ISO 639",
            "source_url": "https://www.iso.org/iso-639-language-codes.html",
            "version": "2024"
        }
    },
    "terms": terms
}))
EOF

import_terminology "ISO 639 Language Codes" "/tmp/iso639_import.json"

echo "=============================================="
echo "Import complete!"
echo "=============================================="
