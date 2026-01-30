"""
Performance testing utilities and benchmark helpers.

Provides tools for measuring API performance and generating
benchmark reports.
"""

import time
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
import json


@dataclass
class BenchmarkResult:
    """Result of a single benchmark run."""
    operation: str
    count: int
    times_ms: list[float] = field(default_factory=list)
    errors: int = 0
    target_ms: float = 0

    @property
    def p50(self) -> float:
        """50th percentile (median)."""
        if not self.times_ms:
            return 0
        return statistics.median(self.times_ms)

    @property
    def p95(self) -> float:
        """95th percentile."""
        if not self.times_ms:
            return 0
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99(self) -> float:
        """99th percentile."""
        if not self.times_ms:
            return 0
        sorted_times = sorted(self.times_ms)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def ops_per_sec(self) -> float:
        """Operations per second based on median time."""
        if self.p50 == 0:
            return 0
        return 1000 / self.p50

    @property
    def success_rate(self) -> float:
        """Percentage of successful operations."""
        total = len(self.times_ms) + self.errors
        if total == 0:
            return 0
        return (len(self.times_ms) / total) * 100

    @property
    def meets_target(self) -> bool:
        """Whether p99 meets the target."""
        if self.target_ms == 0:
            return True
        return self.p99 <= self.target_ms

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON export."""
        return {
            "operation": self.operation,
            "count": self.count,
            "p50_ms": round(self.p50, 2),
            "p95_ms": round(self.p95, 2),
            "p99_ms": round(self.p99, 2),
            "ops_per_sec": round(self.ops_per_sec, 1),
            "errors": self.errors,
            "success_rate": round(self.success_rate, 1),
            "target_ms": self.target_ms,
            "meets_target": self.meets_target
        }


@dataclass
class BenchmarkReport:
    """Complete benchmark report."""
    profile: str
    date: str
    documents_count: int
    templates_count: int
    terms_count: int
    results: list[BenchmarkResult] = field(default_factory=list)

    def add_result(self, result: BenchmarkResult):
        """Add a benchmark result."""
        self.results.append(result)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON export."""
        return {
            "profile": self.profile,
            "date": self.date,
            "counts": {
                "documents": self.documents_count,
                "templates": self.templates_count,
                "terms": self.terms_count
            },
            "operations": [r.to_dict() for r in self.results]
        }

    def to_json(self, indent: int = 2) -> str:
        """Export as JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def print_report(self):
        """Print formatted benchmark report to console."""
        print()
        print("=" * 70)
        print("WIP Performance Benchmark Results")
        print("=" * 70)
        print(f"Date: {self.date}")
        print(f"Profile: {self.profile}")
        print(f"Documents: {self.documents_count:,}")
        print(f"Templates: {self.templates_count}")
        print(f"Terms: {self.terms_count:,}")
        print()
        print(f"{'Operation':<25} {'p50':>8} {'p95':>8} {'p99':>8} {'ops/sec':>10} {'Status':>10}")
        print("-" * 70)

        for result in self.results:
            status = "PASS" if result.meets_target else "FAIL"
            status_color = status
            print(
                f"{result.operation:<25} "
                f"{result.p50:>7.1f}ms "
                f"{result.p95:>7.1f}ms "
                f"{result.p99:>7.1f}ms "
                f"{result.ops_per_sec:>9.1f} "
                f"{status_color:>10}"
            )

        print("=" * 70)

        # Summary
        passed = sum(1 for r in self.results if r.meets_target)
        total = len(self.results)
        print(f"\nSummary: {passed}/{total} operations met targets")


# Target times for different operations (in milliseconds)
PERFORMANCE_TARGETS = {
    "create_document": 100,
    "get_document": 50,
    "list_documents": 200,
    "validate_document": 150,
    "query_documents": 500,
    "bulk_create_100": 2000,
    "term_validation": 50,
    "template_resolution": 100,
}


def measure_operation(
    operation: Callable[[], Any],
    count: int = 100,
    warmup: int = 5
) -> BenchmarkResult:
    """
    Measure an operation's performance.

    Args:
        operation: Function to measure
        count: Number of iterations
        warmup: Number of warmup iterations (not counted)

    Returns:
        BenchmarkResult with timing statistics
    """
    result = BenchmarkResult(operation="custom", count=count)

    # Warmup
    for _ in range(warmup):
        try:
            operation()
        except Exception:
            pass

    # Measured runs
    for _ in range(count):
        start = time.perf_counter()
        try:
            operation()
            elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
            result.times_ms.append(elapsed)
        except Exception:
            result.errors += 1

    return result


class ProgressReporter:
    """Reports progress during long operations."""

    def __init__(self, total: int, operation: str = "Processing"):
        self.total = total
        self.current = 0
        self.operation = operation
        self.start_time = time.time()
        self.last_report = 0

    def update(self, count: int = 1):
        """Update progress."""
        self.current += count

        # Report every 10% or at least every second
        elapsed = time.time() - self.start_time
        progress = self.current / self.total
        if progress - self.last_report >= 0.1 or elapsed - self.last_report >= 1:
            self.last_report = progress
            rate = self.current / elapsed if elapsed > 0 else 0
            eta = (self.total - self.current) / rate if rate > 0 else 0
            print(
                f"\r{self.operation}: {self.current:,}/{self.total:,} "
                f"({progress*100:.1f}%) - {rate:.1f}/s - ETA: {eta:.0f}s",
                end="", flush=True
            )

    def complete(self):
        """Mark as complete."""
        elapsed = time.time() - self.start_time
        rate = self.total / elapsed if elapsed > 0 else 0
        print(
            f"\r{self.operation}: {self.total:,}/{self.total:,} "
            f"(100%) - Completed in {elapsed:.1f}s ({rate:.1f}/s)"
        )


def generate_batch(
    generator: Callable[[int], dict[str, Any]],
    count: int,
    batch_size: int = 100
) -> list[list[dict[str, Any]]]:
    """
    Generate documents in batches for bulk operations.

    Args:
        generator: Function that generates a single document given an index
        count: Total documents to generate
        batch_size: Size of each batch

    Returns:
        List of batches, each containing batch_size documents
    """
    batches = []
    current_batch = []

    for i in range(count):
        doc = generator(i)
        current_batch.append(doc)

        if len(current_batch) >= batch_size:
            batches.append(current_batch)
            current_batch = []

    # Add remaining documents
    if current_batch:
        batches.append(current_batch)

    return batches


def create_benchmark_report(
    profile: str,
    documents_count: int,
    templates_count: int,
    terms_count: int
) -> BenchmarkReport:
    """Create a new benchmark report."""
    return BenchmarkReport(
        profile=profile,
        date=datetime.now().isoformat(),
        documents_count=documents_count,
        templates_count=templates_count,
        terms_count=terms_count
    )


# Utility functions for common benchmark scenarios

def benchmark_sequential_creates(
    create_func: Callable[[dict], Any],
    documents: list[dict],
    operation_name: str = "create_document"
) -> BenchmarkResult:
    """Benchmark sequential document creation."""
    result = BenchmarkResult(
        operation=operation_name,
        count=len(documents),
        target_ms=PERFORMANCE_TARGETS.get(operation_name, 0)
    )

    for doc in documents:
        start = time.perf_counter()
        try:
            create_func(doc)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)
        except Exception:
            result.errors += 1

    return result


def benchmark_bulk_creates(
    bulk_create_func: Callable[[list[dict]], Any],
    batches: list[list[dict]],
    operation_name: str = "bulk_create_100"
) -> BenchmarkResult:
    """Benchmark bulk document creation."""
    result = BenchmarkResult(
        operation=operation_name,
        count=len(batches),
        target_ms=PERFORMANCE_TARGETS.get(operation_name, 0)
    )

    for batch in batches:
        start = time.perf_counter()
        try:
            bulk_create_func(batch)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)
        except Exception:
            result.errors += 1

    return result


def benchmark_reads(
    get_func: Callable[[str], Any],
    document_ids: list[str],
    operation_name: str = "get_document"
) -> BenchmarkResult:
    """Benchmark document reads."""
    result = BenchmarkResult(
        operation=operation_name,
        count=len(document_ids),
        target_ms=PERFORMANCE_TARGETS.get(operation_name, 0)
    )

    for doc_id in document_ids:
        start = time.perf_counter()
        try:
            get_func(doc_id)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)
        except Exception:
            result.errors += 1

    return result


def benchmark_queries(
    query_func: Callable[[dict], Any],
    queries: list[dict],
    operation_name: str = "query_documents"
) -> BenchmarkResult:
    """Benchmark document queries."""
    result = BenchmarkResult(
        operation=operation_name,
        count=len(queries),
        target_ms=PERFORMANCE_TARGETS.get(operation_name, 0)
    )

    for query in queries:
        start = time.perf_counter()
        try:
            query_func(query)
            elapsed = (time.perf_counter() - start) * 1000
            result.times_ms.append(elapsed)
        except Exception:
            result.errors += 1

    return result
