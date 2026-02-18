#!/usr/bin/env python3
"""
Ingest Gateway Stress Test Script

Publishes test data to JetStream ingest subjects and validates results.
Creates prerequisite terminologies/templates before testing.

Usage:
    python scripts/test_ingest_gateway.py [--nats-url nats://localhost:4222] [--count 1000]

Requirements:
    pip install nats-py httpx

Test Categories:
    - Valid terminologies (10)
    - Valid terms bulk (varying sizes)
    - Valid templates (5)
    - Valid documents (configurable, default 1000)
    - Invalid - missing required fields (50)
    - Invalid - bad references (50)
    - Invalid - malformed JSON (20)
    - Duplicate terminologies (10)
"""

import argparse
import asyncio
import json
import random
import string
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import nats
    from nats.js import JetStreamContext
except ImportError:
    print("Error: nats-py not installed. Run: pip install nats-py")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# Configuration
NATS_URL = "nats://localhost:4222"
DEF_STORE_URL = "http://localhost:8002"
TEMPLATE_STORE_URL = "http://localhost:8003"
API_KEY = "dev_master_key_for_testing"

# Test run identifier
TEST_RUN_ID = f"test-{uuid.uuid4().hex[:8]}"


@dataclass
class TestResult:
    """Result of a single test message."""
    correlation_id: str
    category: str
    expected_status: str
    actual_status: Optional[str] = None
    error: Optional[str] = None
    passed: bool = False


@dataclass
class TestStats:
    """Aggregated test statistics."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    timeout: int = 0
    by_category: dict = field(default_factory=dict)


# ============================================================
# Phase 1: Create Prerequisites via REST API
# ============================================================

async def create_test_terminology(client: httpx.AsyncClient, code: str, name: str) -> Optional[str]:
    """Create a terminology via REST API, return terminology_id."""
    try:
        response = await client.post(
            f"{DEF_STORE_URL}/api/def-store/terminologies",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json=[{
                "value": code,
                "label": name,
                "description": f"Test terminology created by stress test {TEST_RUN_ID}"
            }]
        )
        if response.status_code == 200:
            data = response.json()
            return data["results"][0].get("id")
        else:
            print(f"  Warning: Failed to create terminology {code}: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Warning: Error creating terminology {code}: {e}")
        return None


async def create_test_terms(
    client: httpx.AsyncClient,
    terminology_id: str,
    terms: List[dict]
) -> bool:
    """Create terms in a terminology via REST API."""
    try:
        response = await client.post(
            f"{DEF_STORE_URL}/api/def-store/terminologies/{terminology_id}/terms",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json=terms
        )
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"  Warning: Error creating terms: {e}")
        return False


async def create_test_template(
    client: httpx.AsyncClient,
    code: str,
    terminology_id: str
) -> Optional[str]:
    """Create a template that references the test terminology."""
    try:
        response = await client.post(
            f"{TEMPLATE_STORE_URL}/api/template-store/templates",
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
            json=[{
                "value": code,
                "label": f"Ingest Test Template {code}",
                "description": f"Test template for stress test {TEST_RUN_ID}",
                "identity_fields": ["email"],
                "fields": [
                    {"name": "name", "label": "Name", "type": "string", "required": True},
                    {"name": "email", "label": "Email", "type": "string", "required": True},
                    {
                        "name": "status",
                        "label": "Status",
                        "type": "term",
                        "required": False,
                        "terminology_ref": terminology_id
                    }
                ]
            }]
        )
        if response.status_code == 200:
            data = response.json()
            return data["results"][0].get("id")
        else:
            print(f"  Warning: Failed to create template {code}: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Warning: Error creating template {code}: {e}")
        return None


# ============================================================
# Phase 2: Generate Test Messages
# ============================================================

def random_string(length: int = 8) -> str:
    """Generate a random string."""
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def generate_valid_terminology(index: int) -> dict:
    """Generate a valid terminology creation message."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-term-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            "value": f"INGEST_TEST_{TEST_RUN_ID}_{index}",
            "label": f"Ingest Test Terminology {index}",
            "description": f"Created by stress test {TEST_RUN_ID}"
        }
    }


def generate_valid_terms_bulk(terminology_id: str, count: int) -> dict:
    """Generate a bulk terms creation message."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-terms-bulk-{uuid.uuid4().hex[:8]}",
        "payload": {
            "terminology_id": terminology_id,
            "terms": [
                {
                    "value": f"term_{i}",
                    "label": f"Term {i}"
                }
                for i in range(count)
            ]
        }
    }


def generate_valid_template(index: int, terminology_id: str) -> dict:
    """Generate a valid template creation message."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-tpl-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            "value": f"TPL_INGEST_{TEST_RUN_ID}_{index}",
            "label": f"Ingest Test Template {index}",
            "description": f"Created by stress test {TEST_RUN_ID}",
            "identity_fields": ["email"],
            "fields": [
                {"name": "name", "label": "Name", "type": "string", "required": True},
                {"name": "email", "label": "Email", "type": "string", "required": True},
                {
                    "name": "status",
                    "label": "Status",
                    "type": "term",
                    "required": False,
                    "terminology_ref": terminology_id
                }
            ]
        }
    }


def generate_valid_document(template_id: str, index: int) -> dict:
    """Generate a valid document creation message."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-doc-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            "template_id": template_id,
            "data": {
                "name": f"Test Person {index}",
                "email": f"test{index}_{random_string(4)}@example.com"
            }
        }
    }


def generate_invalid_missing_value(index: int) -> dict:
    """Generate terminology missing required 'value' field."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-invalid-missing-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            # Missing 'value' - should fail validation
            "label": "Invalid - No Value"
        }
    }


def generate_invalid_bad_template_ref(index: int) -> dict:
    """Generate document with non-existent template_id."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-invalid-ref-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            "template_id": "TPL-NONEXISTENT-000000",
            "data": {"name": "Should Fail", "email": "fail@example.com"}
        }
    }


def generate_duplicate_terminology(original_value: str, index: int) -> dict:
    """Generate terminology with duplicate value (should fail)."""
    return {
        "correlation_id": f"{TEST_RUN_ID}-dup-{index}-{uuid.uuid4().hex[:8]}",
        "payload": {
            "value": original_value,
            "label": f"Duplicate terminology {index}"
        }
    }


# ============================================================
# Phase 3: Publish Messages and Collect Results
# ============================================================

async def publish_and_collect(
    js: JetStreamContext,
    messages: List[Tuple[str, Union[dict, bytes], str, str]],
    timeout_seconds: float = 120.0
) -> List[TestResult]:
    """
    Publish all messages and collect results from results stream.

    Args:
        js: JetStream context
        messages: List of (subject, message, category, expected_status)
        timeout_seconds: Max time to wait for all results

    Returns:
        List of TestResult with pass/fail status
    """
    results_by_correlation: Dict[str, TestResult] = {}

    # Create/get consumer for results
    try:
        sub = await js.pull_subscribe(
            "wip.ingest.results.>",
            durable=f"stress-test-reader-{TEST_RUN_ID}",
            stream="WIP_INGEST_RESULTS"
        )
    except Exception as e:
        print(f"Error creating results consumer: {e}")
        return []

    # Publish all messages
    print(f"  Publishing {len(messages)} messages...")
    for subject, msg, category, expected in messages:
        if isinstance(msg, bytes):
            try:
                await js.publish(subject, msg)
            except Exception as e:
                print(f"  Error publishing malformed message: {e}")
        else:
            correlation_id = msg.get("correlation_id", f"unknown-{uuid.uuid4().hex[:8]}")
            results_by_correlation[correlation_id] = TestResult(
                correlation_id=correlation_id,
                category=category,
                expected_status=expected
            )
            try:
                await js.publish(subject, json.dumps(msg).encode())
            except Exception as e:
                print(f"  Error publishing message {correlation_id}: {e}")

    # Collect results with timeout
    print(f"  Waiting for results (timeout: {timeout_seconds}s)...")
    start = time.time()
    received = 0
    expected_count = len(results_by_correlation)

    while time.time() - start < timeout_seconds:
        try:
            msgs = await sub.fetch(batch=100, timeout=2)
            for msg in msgs:
                try:
                    data = json.loads(msg.data.decode())
                    cid = data.get("correlation_id")
                    if cid in results_by_correlation:
                        result = results_by_correlation[cid]
                        result.actual_status = data.get("status")
                        result.error = data.get("error")
                        result.passed = result.actual_status == result.expected_status
                        received += 1
                except Exception as e:
                    print(f"  Error parsing result: {e}")
                await msg.ack()

            # Progress update every 100 messages
            if received > 0 and received % 100 == 0:
                elapsed = time.time() - start
                print(f"    Received {received}/{expected_count} results ({elapsed:.1f}s)")

        except asyncio.TimeoutError:
            # Check if all results collected
            all_done = all(
                r.actual_status is not None
                for r in results_by_correlation.values()
            )
            if all_done:
                break

    return list(results_by_correlation.values())


def aggregate_stats(results: List[TestResult]) -> TestStats:
    """Aggregate test results into statistics."""
    stats = TestStats()
    stats.total = len(results)

    for r in results:
        if r.category not in stats.by_category:
            stats.by_category[r.category] = {"passed": 0, "failed": 0, "timeout": 0}

        if r.actual_status is None:
            stats.timeout += 1
            stats.by_category[r.category]["timeout"] += 1
        elif r.passed:
            stats.passed += 1
            stats.by_category[r.category]["passed"] += 1
        else:
            stats.failed += 1
            stats.by_category[r.category]["failed"] += 1

    return stats


# ============================================================
# Phase 4: Main Test Runner
# ============================================================

async def run_stress_test(
    nats_url: str = NATS_URL,
    document_count: int = 1000,
    terminology_count: int = 10,
    template_count: int = 5,
    invalid_count: int = 50,
    terms_per_bulk: int = 50,
) -> bool:
    """Run the full stress test."""

    print("=" * 70)
    print("  INGEST GATEWAY STRESS TEST")
    print("=" * 70)
    print(f"Test Run ID: {TEST_RUN_ID}")
    print(f"NATS URL: {nats_url}")
    print("")

    # Connect to NATS
    print("[Phase 0] Connecting to NATS...")
    try:
        nc = await nats.connect(nats_url)
        js = nc.jetstream()
        print("  Connected to NATS")
    except Exception as e:
        print(f"  ERROR: Failed to connect to NATS: {e}")
        return False

    # Verify streams exist
    try:
        await js.stream_info("WIP_INGEST")
        print("  WIP_INGEST stream exists")
    except Exception:
        print("  ERROR: WIP_INGEST stream not found. Is ingest-gateway running?")
        await nc.close()
        return False

    try:
        await js.stream_info("WIP_INGEST_RESULTS")
        print("  WIP_INGEST_RESULTS stream exists")
    except Exception:
        print("  ERROR: WIP_INGEST_RESULTS stream not found. Is ingest-gateway running?")
        await nc.close()
        return False

    print("")

    # Phase 1: Create prerequisites via REST API
    print("[Phase 1] Creating prerequisites via REST API...")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        # Create test terminology for documents
        test_term_code = f"STRESS_TEST_{TEST_RUN_ID}"
        test_terminology_id = await create_test_terminology(
            http_client, test_term_code, "Stress Test Terminology"
        )
        if test_terminology_id:
            print(f"  Created terminology: {test_terminology_id}")

            # Create test terms
            await create_test_terms(http_client, test_terminology_id, [
                {"value": "active", "label": "Active"},
                {"value": "pending", "label": "Pending"},
            ])
            print("  Created test terms")
        else:
            print("  WARNING: Could not create test terminology, document tests may fail")
            test_terminology_id = "TERM-UNKNOWN"

        # Create test template for documents
        test_template_value = f"STRESS_TEST_TPL_{TEST_RUN_ID}"
        test_template_id = await create_test_template(
            http_client, test_template_value, test_terminology_id
        )
        if test_template_id:
            print(f"  Created template: {test_template_id}")
        else:
            print("  WARNING: Could not create test template, document tests may fail")
            test_template_id = "TPL-UNKNOWN"

    print("")

    # Phase 2: Generate test messages
    print("[Phase 2] Generating test messages...")
    messages: List[Tuple[str, Union[dict, bytes], str, str]] = []

    # Valid terminologies
    for i in range(terminology_count):
        msg = generate_valid_terminology(i)
        messages.append(("wip.ingest.terminologies.create", msg, "valid_terminology", "success"))

    # Valid terms bulk
    if test_terminology_id and test_terminology_id != "TERM-UNKNOWN":
        # Create a new terminology for bulk terms test via ingest
        bulk_term_msg = generate_valid_terminology(9999)
        messages.append(("wip.ingest.terminologies.create", bulk_term_msg, "valid_terminology_for_terms", "success"))

    # Valid templates
    for i in range(template_count):
        msg = generate_valid_template(i, test_terminology_id)
        messages.append(("wip.ingest.templates.create", msg, "valid_template", "success"))

    # Valid documents
    for i in range(document_count):
        msg = generate_valid_document(test_template_id, i)
        messages.append(("wip.ingest.documents.create", msg, "valid_document", "success"))

    # Invalid - missing fields
    for i in range(invalid_count):
        msg = generate_invalid_missing_value(i)
        messages.append(("wip.ingest.terminologies.create", msg, "invalid_missing", "failed"))

    # Invalid - bad references
    for i in range(invalid_count):
        msg = generate_invalid_bad_template_ref(i)
        messages.append(("wip.ingest.documents.create", msg, "invalid_reference", "failed"))

    # Duplicate terminologies (use the first generated terminology value)
    first_term_value = f"INGEST_TEST_{TEST_RUN_ID}_0"
    for i in range(10):
        msg = generate_duplicate_terminology(first_term_value, i)
        messages.append(("wip.ingest.terminologies.create", msg, "duplicate", "failed"))

    print(f"  Total messages: {len(messages)}")
    print(f"    Valid terminologies: {terminology_count}")
    print(f"    Valid templates: {template_count}")
    print(f"    Valid documents: {document_count}")
    print(f"    Invalid (missing fields): {invalid_count}")
    print(f"    Invalid (bad refs): {invalid_count}")
    print(f"    Duplicates: 10")
    print("")

    # Phase 3: Publish and collect results
    print("[Phase 3] Publishing messages and collecting results...")
    start_time = time.time()
    results = await publish_and_collect(js, messages, timeout_seconds=max(120, document_count / 10))
    elapsed = time.time() - start_time
    print(f"  Collection completed in {elapsed:.2f}s")
    print("")

    # Phase 4: Report results
    print("[Phase 4] Results Summary")
    print("-" * 70)

    stats = aggregate_stats(results)

    for cat in sorted(stats.by_category.keys()):
        s = stats.by_category[cat]
        total_cat = s['passed'] + s['failed'] + s['timeout']
        status_emoji = "✓" if s['failed'] == 0 and s['timeout'] == 0 else "✗"
        print(f"  {status_emoji} {cat}: {s['passed']}/{total_cat} passed, {s['failed']} failed, {s['timeout']} timeout")

    print("-" * 70)
    total_tests = stats.passed + stats.failed + stats.timeout
    success_rate = (stats.passed / total_tests * 100) if total_tests > 0 else 0
    print(f"  TOTAL: {stats.passed}/{total_tests} passed ({success_rate:.1f}%)")
    print(f"  TIME: {elapsed:.2f}s ({len(messages)/elapsed:.1f} msg/sec)")

    # Show failed tests details (first 5)
    failed_results = [r for r in results if not r.passed and r.actual_status is not None]
    if failed_results:
        print("")
        print("  First 5 unexpected failures:")
        for r in failed_results[:5]:
            print(f"    - {r.correlation_id}: expected={r.expected_status}, got={r.actual_status}")
            if r.error:
                print(f"      Error: {r.error[:100]}")

    print("=" * 70)

    # Cleanup
    await nc.close()

    # Return success if no unexpected failures
    # Note: "failed" status is expected for invalid messages
    unexpected_failures = sum(
        1 for r in results
        if r.actual_status is not None and not r.passed
    )
    return unexpected_failures == 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Ingest Gateway Stress Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run with defaults (1000 documents)
    python scripts/test_ingest_gateway.py

    # Run with more documents
    python scripts/test_ingest_gateway.py --count 5000

    # Custom NATS URL
    python scripts/test_ingest_gateway.py --nats-url nats://wip-nats:4222
        """
    )
    parser.add_argument(
        "--count", type=int, default=1000,
        help="Number of valid documents to test (default: 1000)"
    )
    parser.add_argument(
        "--nats-url", default=NATS_URL,
        help=f"NATS URL (default: {NATS_URL})"
    )
    parser.add_argument(
        "--terminology-count", type=int, default=10,
        help="Number of terminologies to create (default: 10)"
    )
    parser.add_argument(
        "--template-count", type=int, default=5,
        help="Number of templates to create (default: 5)"
    )
    parser.add_argument(
        "--invalid-count", type=int, default=50,
        help="Number of invalid messages per category (default: 50)"
    )

    args = parser.parse_args()

    success = asyncio.run(run_stress_test(
        nats_url=args.nats_url,
        document_count=args.count,
        terminology_count=args.terminology_count,
        template_count=args.template_count,
        invalid_count=args.invalid_count,
    ))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
