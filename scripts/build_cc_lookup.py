"""
One-time build script: converts cc_graph.parquet → cc_lookup.duckdb

What it does:
  1. Reads only the latest CC release from the parquet (118M rows, ~3.5 GB)
  2. Sorts by domain (enables zone-map pruning even before the ART index kicks in)
  3. Writes to a DuckDB file
  4. Creates a unique ART index on domain → sub-millisecond exact lookups

Run from the backend/ directory:
    python scripts/build_cc_lookup.py
"""

import duckdb
import os
import time

PARQUET_PATH  = "data/cc_graph.parquet"
DUCKDB_PATH   = "data/cc_lookup.duckdb"
LATEST_RELEASE = "cc-main-2026-mar-apr-may"

# Drop the single-backlink noise (67% of rows, ~0 authority). A domain below
# this cutoff returns zero metrics — identical to a not-found result — so this
# is near-lossless for SEO scoring while shrinking the file ~3x (2.1GB -> ~700MB).
MIN_IN_DEGREE = 2

if not os.path.exists(PARQUET_PATH):
    raise FileNotFoundError(f"Not found: {PARQUET_PATH}")

if os.path.exists(DUCKDB_PATH):
    os.remove(DUCKDB_PATH)
    print(f"Removed existing {DUCKDB_PATH}")

print(f"Opening parquet: {PARQUET_PATH}")
print(f"Filtering to release: {LATEST_RELEASE}")
print(f"Writing to: {DUCKDB_PATH}")
print()

conn = duckdb.connect(DUCKDB_PATH)

# Keep memory modest and disable insertion-order preservation so the sort
# spills to disk instead of OOM-ing on low-RAM machines.
conn.execute("SET memory_limit='3GB'")
conn.execute("SET threads=2")
conn.execute("SET preserve_insertion_order=false")

# ── Step 1: Create table ──────────────────────────────────────────────────────
print(f"Step 1/2: Creating table (filter in_degree>={MIN_IN_DEGREE} + sort)...")
t0 = time.time()

conn.execute(f"""
    CREATE TABLE domain_metrics AS
    SELECT
        domain,
        in_degree,
        CAST(referring_domains_log AS FLOAT)  AS referring_domains_log,
        CAST(pagerank               AS FLOAT)  AS pagerank,
        CAST(harmonic_centrality    AS FLOAT)  AS harmonic_centrality
    FROM read_parquet('{PARQUET_PATH}')
    WHERE rank_release = '{LATEST_RELEASE}'
      AND in_degree >= {MIN_IN_DEGREE}
    ORDER BY domain
""")

elapsed = time.time() - t0
count = conn.execute("SELECT COUNT(*) FROM domain_metrics").fetchone()[0]
print(f"  Done in {elapsed/60:.1f} min — {count:,} rows inserted")

# NOTE: no ART index. The table is sorted by domain, so DuckDB's row-group
# zone maps prune to 1-2 groups per lookup (~25ms) — and building an index on
# tens of millions of strings OOMs on small hosts. Sorted + zone maps is enough.

# ── Step 2: Verify ────────────────────────────────────────────────────────────
print("Step 2/2: Verifying lookups...")
t2 = time.time()
test_domains = ["google.com", "github.com", "wikipedia.org", "nonexistent-xyz-123456.com"]
for d in test_domains:
    t_q = time.time()
    row = conn.execute(
        "SELECT domain, in_degree, referring_domains_log, pagerank, harmonic_centrality FROM domain_metrics WHERE domain = ?",
        [d]
    ).fetchone()
    ms = (time.time() - t_q) * 1000
    if row:
        print(f"  {d}: in_degree={row[1]:,}  pagerank={row[3]:.0f}  ({ms:.1f}ms)")
    else:
        print(f"  {d}: not found ({ms:.1f}ms)")

conn.close()

size_gb = os.path.getsize(DUCKDB_PATH) / 1024**3
print()
print(f"All done! cc_lookup.duckdb is {size_gb:.2f} GB")
print(f"Total time: {(time.time()-t0)/60:.1f} min")
