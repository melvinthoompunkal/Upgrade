"""Saved-summary scale acceptance, with no Git/provider calls or full detail decoding."""

import json
import os
from pathlib import Path
from statistics import quantiles
from time import perf_counter

from app.api.storage import RunStore, now
from app.catalog_store import CatalogStore
from app.relevance_store import RelevanceStore
from app.tools.runner import PROJECT_ROOT
from scripts.release_fixtures import SIZES, seed_catalog


def test_saved_query_payload_and_p95_at_declared_sizes(tmp_path):
    runs = RunStore(tmp_path / "scale.sqlite3")
    catalogs = CatalogStore(runs)
    receipts = []
    for size in SIZES:
        catalog, assessment = seed_catalog(runs, size, poison_details=True)
        query = dict(
            assessment_id=assessment.id,
            mode="relevant",
            q="capability",
            category="sequences",
            status="supported",
        )
        # Warm the index/connection path before the measured samples.
        catalogs.page("local-workspace", catalog.id, 1, **query)
        measurements = []
        for _ in range(30):
            start = perf_counter()
            page = catalogs.page("local-workspace", catalog.id, 1, **query)
            measurements.append((perf_counter() - start) * 1000)
        payload_bytes = len(page.model_dump_json().encode())
        p95 = quantiles(measurements, n=100)[94]
        assert len(page.items) == 25 and page.matching_filter == size
        assert all(item.aliases == [] for item in page.items)
        assert payload_bytes <= 100 * 1024 and p95 <= 300
        # Every equal-score seek page must be complete, ordered and disjoint.
        ids = []
        while True:
            assert page.matching_filter == size
            ids.extend(item.feature_id for item in page.items)
            if page.next_cursor is None:
                break
            page = catalogs.page("local-workspace", catalog.id, 1, **query, cursor=page.next_cursor)
        assert ids == [f"{index:032x}" for index in range(size)]
        last = catalogs.page("local-workspace", catalog.id, 1, q=f"{size:05}")
        assert last.matching_filter == 1
        assert RelevanceStore(runs).get("local-workspace", assessment.id).counts.discovered == size
        receipts.append(
            {
                "rows": size,
                "samples": 30,
                "p95_ms": round(p95, 3),
                "page_bytes": payload_bytes,
                "walked_entries": len(ids),
                "all_pages_ordered_without_duplicates_or_omissions": True,
            }
        )
    destination = Path(os.environ.get("GRAFT_SCALE_RECEIPT", PROJECT_ROOT / "data/f5-scale.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "measured_at": now(),
                "scope": "Warm SQLite summaries; synthetic rows, no provider/network latency.",
                "measurements": receipts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
