"""
scripts/download_cc_graph.py
============================
ONE-TIME SETUP SCRIPT — run this once to build the local CC graph database.

Downloads the Common Crawl domain-level web graph ranks file (PageRank +
Harmonic Centrality) and the vertices file (domain names + in-degree), then
imports them into a local SQLite database for O(1) inference-time lookups.

Usage — single release (same as before):
    python scripts/download_cc_graph.py
    python scripts/download_cc_graph.py --release cc-main-2024-oct-nov-dec

Usage — merge ALL known releases (recommended for maximum domain coverage):
    python scripts/download_cc_graph.py --merge-all

    This downloads 5 releases (~2.5 GB total compressed) and merges them into
    one database. Domains in multiple releases get:
      - PageRank / HC from the LATEST release (most current authority data)
      - in_degree = MAX across all releases (broadest link signal)
    Final DB is ~600-900 MB (overlap means it is NOT 5x the single-release size).

Usage — specific releases to merge:
    python scripts/download_cc_graph.py \\
        --releases cc-main-2024-25-dec-jan-feb cc-main-2024-oct-nov-dec cc-main-2024-jun-jul-aug

The resulting database is saved to:
    backend/data/cc_graph.db

Database schema:
    CREATE TABLE cc_domains (
        domain                TEXT PRIMARY KEY,   -- eTLD+1, e.g. "example.com"
        pagerank              REAL,
        harmonic_centrality   REAL,
        in_degree             INTEGER,            -- best referring domains count (raw)
        referring_domains_log REAL                -- log1p(in_degree)
    )

File format (CC web graph ranks, tab-separated, node order aligned with vertices):
    <harmonic_centrality>\\t<pagerank>

Vertices file (for domain names + in-degree):
    <node_id>\\t<domain_reverse>\\t<in_degree>\\t<out_degree>

NOTE: The full domain graph has ~100-200 million nodes per release.
Each download is ~500 MB compressed; import takes ~5-15 minutes per release.
Merging all 5 releases takes ~30-60 minutes total but only needs to be done once.
"""

import sys
import gzip
import io
import math
import sqlite3
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
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH  = DATA_DIR / "cc_graph.db"

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
# DATABASE WRITERS
# ══════════════════════════════════════════════════════════════════

def _create_db(db_path: Path) -> sqlite3.Connection:
    """Create a fresh SQLite database with the cc_domains schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
        logger.info(f"Removed existing database: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE cc_domains (
            domain                TEXT PRIMARY KEY,
            pagerank              REAL NOT NULL DEFAULT 0.0,
            harmonic_centrality   REAL NOT NULL DEFAULT 0.0,
            in_degree             INTEGER NOT NULL DEFAULT 0,
            referring_domains_log REAL NOT NULL DEFAULT 0.0
        )
    """)
    con.commit()
    logger.info(f"Created database: {db_path}")
    return con


def _write_rows_batch(
    con: sqlite3.Connection,
    rows: list[tuple],
    merge_mode: bool = False,
) -> int:
    """
    Write rows to the DB in batches of 100k.

    Single-release mode:   INSERT OR REPLACE (overwrite)
    Merge mode:            INSERT OR REPLACE with MAX(in_degree) logic.
                           Because we process oldest → newest, the final INSERT
                           for each domain uses the latest release's PR/HC (correct),
                           and we manually keep the max in_degree seen so far.
    """
    cur       = con.cursor()
    inserted  = 0
    batch     = []

    if merge_mode:
        # In merge mode we use a conflict strategy:
        # - pagerank / harmonic_centrality: new value wins (latest release)
        # - in_degree: take MAX of existing and new
        upsert_sql = """
            INSERT INTO cc_domains (domain, pagerank, harmonic_centrality, in_degree, referring_domains_log)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                pagerank              = excluded.pagerank,
                harmonic_centrality   = excluded.harmonic_centrality,
                in_degree             = MAX(cc_domains.in_degree, excluded.in_degree),
                referring_domains_log = MAX(cc_domains.referring_domains_log, excluded.referring_domains_log)
        """
    else:
        upsert_sql = "INSERT OR REPLACE INTO cc_domains VALUES (?,?,?,?,?)"

    for row in rows:
        batch.append(row)
        if len(batch) >= 100_000:
            cur.executemany(upsert_sql, batch)
            con.commit()
            inserted += len(batch)
            batch = []
            logger.info(f"  Written {inserted:,} rows...")

    if batch:
        cur.executemany(upsert_sql, batch)
        con.commit()
        inserted += len(batch)

    return inserted


def _finalize_db(con: sqlite3.Connection, db_path: Path):
    """Create index and log final stats."""
    logger.info("Creating index on domain column...")
    con.execute("CREATE INDEX IF NOT EXISTS idx_domain ON cc_domains(domain)")
    con.commit()

    count   = con.execute("SELECT COUNT(*) FROM cc_domains").fetchone()[0]
    size_mb = db_path.stat().st_size / 1024 / 1024
    logger.info(f"Database ready: {count:,} domains, {size_mb:.0f} MB → {db_path}")
    con.close()


# ══════════════════════════════════════════════════════════════════
# HIGH-LEVEL OPERATIONS
# ══════════════════════════════════════════════════════════════════

def process_release(release_key: str, vertices_only: bool = False) -> list[tuple]:
    """
    Download and parse one release. Returns list of row tuples.
    Does NOT write to the DB — caller decides how to write.
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
    """Download and build DB from a single release (original behaviour)."""
    rows = process_release(release_key, vertices_only)
    con  = _create_db(DB_PATH)
    _write_rows_batch(con, rows, merge_mode=False)
    _finalize_db(con, DB_PATH)


def build_merged(release_keys: list[str], vertices_only: bool = False):
    """
    Download and merge multiple releases into a single database.

    Merge strategy:
      - PageRank / Harmonic Centrality: latest release wins (most current data)
      - in_degree: MAX across all releases (broadest link signal)

    Process releases oldest → newest so the last write for each domain
    comes from the most recent release.
    """
    logger.info(f"\nMerge mode: {len(release_keys)} releases to process")
    for i, key in enumerate(release_keys):
        logger.info(f"  {i+1}. {key}  ({RELEASES[key]['label']})")

    con = _create_db(DB_PATH)

    total_written = 0
    for i, key in enumerate(release_keys):
        logger.info(f"\n[{i+1}/{len(release_keys)}] Starting release: {key}")
        rows = process_release(key, vertices_only)
        written = _write_rows_batch(con, rows, merge_mode=True)
        total_written += written
        logger.info(f"  Release done: {written:,} rows written (total DB writes so far: {total_written:,})")
        del rows  # free memory before next release

    _finalize_db(con, DB_PATH)
    logger.info(f"\nAll releases merged. Total rows written: {total_written:,}")
    logger.info("Note: unique domain count is much lower due to overlap across releases.")


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
  # Single release (fastest, ~300-600 MB):
  python scripts/download_cc_graph.py

  # Merge all 5 known releases (recommended, ~600-900 MB final DB):
  python scripts/download_cc_graph.py --merge-all

  # Merge just 3 releases:
  python scripts/download_cc_graph.py --releases cc-main-2024-25-dec-jan-feb cc-main-2024-oct-nov-dec cc-main-2024-jun-jul-aug

  # Skip PageRank/HC (vertices only, much faster):
  python scripts/download_cc_graph.py --merge-all --vertices-only
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
        help="Only download vertices (skip ranks — PageRank/HC will be 0)"
    )
    args = parser.parse_args()

    if args.merge_all:
        build_merged(MERGE_ALL_ORDER, args.vertices_only)

    elif args.releases:
        # User specified a custom list — process oldest to newest by list order
        build_merged(args.releases, args.vertices_only)

    else:
        # Single release
        release = args.release or DEFAULT_RELEASE
        build_single(release, args.vertices_only)

    logger.info("\nDone! Start the server — cc_graph lookups are now active.")


if __name__ == "__main__":
    main()
