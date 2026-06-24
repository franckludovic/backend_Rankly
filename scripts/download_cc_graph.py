"""
scripts/download_cc_graph.py
============================
ONE-TIME SETUP SCRIPT- run this once to build cc_graph.parquet.

Downloads the Common Crawl domain-level web graph ranks file (PageRank +
Harmonic Centrality) and the vertices file (domain names + in-degree), then
writes a single sorted Parquet file for production inference-time lookups.

Usage- single release (fastest, ~300-600 MB download):
    python scripts/download_cc_graph.py
    python scripts/download_cc_graph.py --release cc-main-2024-oct-nov-dec

Usage- merge ALL known releases (recommended for maximum domain coverage):
    python scripts/download_cc_graph.py --merge-all

    Domains in multiple releases get:
      - PageRank / HC from the LATEST release (most current authority data)
      - in_degree = MAX across all releases (broadest link signal)

Usage- specific releases to merge:
    python scripts/download_cc_graph.py \\
        --releases cc-main-2024-25-dec-jan-feb cc-main-2024-oct-nov-dec cc-main-2024-jun-jul-aug

Output:
    backend/data/cc_graph.parquet

    Sorted by domain, ZSTD-compressed.
    DuckDB uses row-group min/max stats to find any domain without a full scan.

Parquet schema:
    domain                TEXT    -- eTLD+1, e.g. "example.com"
    pagerank              DOUBLE
    harmonic_centrality   DOUBLE
    in_degree             INT64   -- raw referring-domains count
    referring_domains_log DOUBLE  -- log1p(in_degree)

File format (CC web graph ranks, tab-separated, node order aligned with vertices):
    <harmonic_centrality>\\t<pagerank>

Vertices file (for domain names + in-degree):
    <node_id>\\t<domain_reverse>\\t<in_degree>\\t<out_degree>

NOTE: The full domain graph has ~100-200 million nodes per release.
Each download is ~500 MB compressed; building takes ~5-15 minutes per release.
"""

import sys
import gzip
import io
import math
import argparse
import logging
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
DATA_DIR      = BASE_DIR / "data"
PARQUET_PATH  = DATA_DIR / "cc_graph.parquet"

# ── All known CC domain-level web graph releases ──────────────────
# Ordered newest → oldest so --merge-all processes latest data last
# (latest release wins for PageRank/HC when merging).
CC_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"

RELEASES = {
    # ── 2024/2025 releases (newest first) ──────────────────────────
    "cc-main-2024-25-dec-jan-feb": {
        "label"   : "Dec 2024 / Jan-Feb 2025",
        "ranks"   : f"{CC_BASE}/cc-main-2024-25-dec-jan-feb/domain/cc-main-2024-25-dec-jan-feb-domain-ranks.txt.gz",
        "vertices": f"{CC_BASE}/cc-main-2024-25-dec-jan-feb/domain/cc-main-2024-25-dec-jan-feb-domain-vertices.txt.gz",
    },
    "cc-main-2024-oct-nov-dec": {
        "label"   : "Oct-Dec 2024",
        "ranks"   : f"{CC_BASE}/cc-main-2024-oct-nov-dec/domain/cc-main-2024-oct-nov-dec-domain-ranks.txt.gz",
        "vertices": f"{CC_BASE}/cc-main-2024-oct-nov-dec/domain/cc-main-2024-oct-nov-dec-domain-vertices.txt.gz",
    },
    "cc-main-2024-jun-jul-aug": {
        "label"   : "Jun-Aug 2024",
        "ranks"   : f"{CC_BASE}/cc-main-2024-jun-jul-aug/domain/cc-main-2024-jun-jul-aug-domain-ranks.txt.gz",
        "vertices": f"{CC_BASE}/cc-main-2024-jun-jul-aug/domain/cc-main-2024-jun-jul-aug-domain-vertices.txt.gz",
    },
    "cc-main-2024-may-jun-jul": {
        "label"   : "May-Jul 2024",
        "ranks"   : f"{CC_BASE}/cc-main-2024-may-jun-jul/domain/cc-main-2024-may-jun-jul-domain-ranks.txt.gz",
        "vertices": f"{CC_BASE}/cc-main-2024-may-jun-jul/domain/cc-main-2024-may-jun-jul-domain-vertices.txt.gz",
    },
    "cc-main-2024-feb-mar-apr": {
        "label"   : "Feb-Apr 2024",
        "ranks"   : f"{CC_BASE}/cc-main-2024-feb-mar-apr/domain/cc-main-2024-feb-mar-apr-domain-ranks.txt.gz",
        "vertices": f"{CC_BASE}/cc-main-2024-feb-mar-apr/domain/cc-main-2024-feb-mar-apr-domain-vertices.txt.gz",
    },
}

# Processing order for --merge-all: oldest first so newest wins on conflict
MERGE_ALL_ORDER = [
    "cc-main-2024-feb-mar-apr",
    "cc-main-2024-may-jun-jul",
    "cc-main-2024-jun-jul-aug",
    "cc-main-2024-oct-nov-dec",
    "cc-main-2024-25-dec-jan-feb",  # processed last → wins for PR/HC
]

DEFAULT_RELEASE = "cc-main-2024-oct-nov-dec"


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def reverse_domain(rev: str) -> str:
    """Convert reverse-notation domain 'com.example.www' → 'www.example.com'."""
    return ".".join(reversed(rev.strip().split(".")))


def stream_download(url: str, label: str) -> bytes:
    """Stream-download a URL with progress logging. Returns raw bytes."""
    logger.info(f"Downloading {label}:\n  {url}")
    try:
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()
    except requests.HTTPError as e:
        logger.error(f"HTTP error downloading {label}: {e}")
        raise

    chunks = []
    total  = int(resp.headers.get("content-length", 0))
    done   = 0
    last_pct = -1

    for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
        chunks.append(chunk)
        done += len(chunk)
        pct   = int(done / total * 100) if total else 0
        if pct != last_pct and pct % 10 == 0:
            logger.info(f"  {label}: {pct}%  ({done // 1024 // 1024} MB)")
            last_pct = pct

    logger.info(f"  {label}: done ({done // 1024 // 1024} MB)")
    return b"".join(chunks)


# ══════════════════════════════════════════════════════════════════
# PARSERS
# ══════════════════════════════════════════════════════════════════

def parse_vertices(gz_data: bytes) -> dict[str, int]:
    """
    Parse vertices file → {domain: in_degree}.

    Line format (tab-separated):
        <node_id>  <domain_reverse>  <in_degree>  <out_degree>
    """
    domain_indegree: dict[str, int] = {}
    skipped = 0

    with gzip.open(io.BytesIO(gz_data), "rt", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                skipped += 1
                continue
            try:
                domain_rev = parts[1]
                in_degree  = int(parts[2])
                domain     = reverse_domain(domain_rev)
                domain_indegree[domain] = in_degree
            except (ValueError, IndexError):
                skipped += 1

            if i % 5_000_000 == 0 and i > 0:
                logger.info(f"  Vertices: {i:,} lines, {len(domain_indegree):,} domains parsed...")

    logger.info(f"  Vertices done: {len(domain_indegree):,} domains, {skipped} skipped")
    return domain_indegree


def parse_ranks(gz_data: bytes) -> list[tuple[float, float]]:
    """
    Parse ranks file → list of (harmonic_centrality, pagerank) by node order.

    Line format (tab-separated):
        <harmonic_centrality>  <pagerank>
    Lines are aligned with the vertices file by node ID order.
    """
    ranks: list[tuple[float, float]] = []
    skipped = 0

    with gzip.open(io.BytesIO(gz_data), "rt", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                ranks.append((0.0, 0.0))
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                ranks.append((0.0, 0.0))
                skipped += 1
                continue
            try:
                hc = float(parts[0])
                pr = float(parts[1])
                ranks.append((hc, pr))
            except ValueError:
                ranks.append((0.0, 0.0))
                skipped += 1

            if i % 5_000_000 == 0 and i > 0:
                logger.info(f"  Ranks: {i:,} lines parsed...")

    logger.info(f"  Ranks done: {len(ranks):,} entries, {skipped} parse errors")
    return ranks


def build_rows(
    domain_indegree: dict[str, int],
    ranks: list[tuple[float, float]],
) -> list[tuple]:
    """
    Join vertices + ranks into a list of DB row tuples:
        (domain, pagerank, harmonic_centrality, in_degree, referring_domains_log)
    """
    rows = []
    domains_ordered = list(domain_indegree.keys())

    for i, domain in enumerate(domains_ordered):
        in_deg = domain_indegree[domain]
        hc, pr = ranks[i] if i < len(ranks) else (0.0, 0.0)
        ref_log = math.log1p(in_deg)
        rows.append((domain, pr, hc, in_deg, ref_log))

    return rows


# ══════════════════════════════════════════════════════════════════
# PARQUET WRITER
# ══════════════════════════════════════════════════════════════════

def _write_parquet(rows: list[tuple], parquet_path: Path):
    """
    Write rows to a ZSTD-compressed Parquet file sorted by domain.

    Sorting is critical: DuckDB uses per-row-group min/max statistics to
    skip irrelevant row groups on  WHERE domain = ?  queries.  Without
    sorting, every lookup would scan the entire file.

    Requires:  pip install polars pyarrow
    """
    try:
        import polars as pl
    except ImportError:
        logger.error("polars not installed. Run: pip install polars pyarrow")
        sys.exit(1)

    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Building Parquet: {len(rows):,} rows → sorting by domain...")
    df = pl.DataFrame(
        rows,
        schema={
            "domain"                : pl.Utf8,
            "pagerank"              : pl.Float64,
            "harmonic_centrality"   : pl.Float64,
            "in_degree"             : pl.Int64,
            "referring_domains_log" : pl.Float64,
        },
        orient="row",
    ).sort("domain")

    df.write_parquet(str(parquet_path), compression="zstd", statistics=True)
    size_mb = parquet_path.stat().st_size / 1024 / 1024
    logger.info(f"Parquet ready: {len(df):,} domains, {size_mb:.0f} MB → {parquet_path}")


def _merge_rows(existing: dict, new_rows: list[tuple]) -> dict:
    """
    Merge new_rows into an existing domain→row dict.

    Strategy (same as before):
      - pagerank / harmonic_centrality: new value wins (latest release)
      - in_degree: MAX across releases (broadest link signal)
    """
    for domain, pr, hc, in_deg, ref_log in new_rows:
        if domain in existing:
            old = existing[domain]
            best_in = max(old[3], in_deg)
            existing[domain] = (domain, pr, hc, best_in, math.log1p(best_in))
        else:
            existing[domain] = (domain, pr, hc, in_deg, ref_log)
    return existing


# ══════════════════════════════════════════════════════════════════
# HIGH-LEVEL OPERATIONS
# ══════════════════════════════════════════════════════════════════

def process_release(release_key: str, vertices_only: bool = False) -> list[tuple]:
    """
    Download and parse one release. Returns list of row tuples.
    Does NOT write to the DB- caller decides how to write.
    """
    urls  = RELEASES[release_key]
    label = urls["label"]
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing release: {label}  ({release_key})")
    logger.info(f"{'='*60}")

    # 1. Vertices
    vertices_gz = stream_download(urls["vertices"], f"[{label}] vertices")
    domain_indegree = parse_vertices(vertices_gz)
    del vertices_gz

    # 2. Ranks
    if vertices_only:
        logger.warning("--vertices-only: PageRank and Harmonic Centrality will be 0 for this release.")
        ranks = []
    else:
        ranks_gz = stream_download(urls["ranks"], f"[{label}] ranks")
        ranks    = parse_ranks(ranks_gz)
        del ranks_gz

    # 3. Build rows
    rows = build_rows(domain_indegree, ranks)
    logger.info(f"  Release {label}: {len(rows):,} domain rows built")
    return rows


def build_single(release_key: str, vertices_only: bool = False):
    """Download one release and write cc_graph.parquet."""
    rows = process_release(release_key, vertices_only)
    _write_parquet(rows, PARQUET_PATH)


def build_merged(release_keys: list[str], vertices_only: bool = False):
    """
    Download and merge multiple releases into a single cc_graph.parquet.

    Merge strategy:
      - PageRank / Harmonic Centrality: latest release wins (most current data)
      - in_degree: MAX across all releases (broadest link signal)

    Process releases oldest → newest so the last write for each domain
    comes from the most recent release.
    """
    logger.info(f"\nMerge mode: {len(release_keys)} releases to process")
    for i, key in enumerate(release_keys):
        logger.info(f"  {i+1}. {key}  ({RELEASES[key]['label']})")

    merged: dict = {}
    for i, key in enumerate(release_keys):
        logger.info(f"\n[{i+1}/{len(release_keys)}] Starting release: {key}")
        rows = process_release(key, vertices_only)
        merged = _merge_rows(merged, rows)
        logger.info(f"  Release merged: {len(merged):,} unique domains so far")
        del rows  # free memory before next release

    logger.info(f"\nAll releases merged: {len(merged):,} unique domains total")
    _write_parquet(list(merged.values()), PARQUET_PATH)


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Download and build the CC domain-level web graph SQLite database.\n"
            "Use --merge-all for maximum domain coverage (recommended).\n"
            "Use --releases to merge a custom subset of releases."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single release (fastest, ~300-600 MB download):
  python scripts/download_cc_graph.py

  # Merge all 5 known releases (recommended, ~1-2 GB RAM during build):
  python scripts/download_cc_graph.py --merge-all

  # Merge just 3 releases:
  python scripts/download_cc_graph.py --releases cc-main-2024-25-dec-jan-feb cc-main-2024-oct-nov-dec cc-main-2024-jun-jul-aug

  # Skip PageRank/HC (vertices only, much faster):
  python scripts/download_cc_graph.py --merge-all --vertices-only

Output: backend/data/cc_graph.parquet (sorted by domain, ZSTD-compressed)
        """
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--release",
        default=None,
        choices=list(RELEASES.keys()),
        help=f"Single release to use (default: {DEFAULT_RELEASE})"
    )
    group.add_argument(
        "--merge-all",
        action="store_true",
        help="Download and merge all 5 known 2024/2025 releases (recommended)"
    )
    group.add_argument(
        "--releases",
        nargs="+",
        choices=list(RELEASES.keys()),
        metavar="RELEASE",
        help="Specific releases to download and merge (space-separated)"
    )
    parser.add_argument(
        "--vertices-only",
        action="store_true",
        help="Only download vertices (skip ranks- PageRank/HC will be 0)"
    )
    args = parser.parse_args()

    if args.merge_all:
        build_merged(MERGE_ALL_ORDER, args.vertices_only)

    elif args.releases:
        # User specified a custom list- process oldest to newest by list order
        build_merged(args.releases, args.vertices_only)

    else:
        # Single release
        release = args.release or DEFAULT_RELEASE
        build_single(release, args.vertices_only)

    logger.info("\nDone! Drop cc_graph.parquet into backend/data/ and start the server.")


if __name__ == "__main__":
    main()
