#!/usr/bin/env python3
"""
Offline vector ingestion script — embeds mall entity descriptions and
upserts them into Chroma (one collection per mall).

Run this once after converting raw mall data to canonical format, and
again whenever mall data is refreshed.

Usage:
    # Ingest a single mall
    python -m scripts.ingest_vectors --mall-id al_nakheel_plaza_28

    # Ingest all malls found in backend/data/canonical/
    python -m scripts.ingest_vectors --all

    # Dry-run: show what would be ingested without embedding
    python -m scripts.ingest_vectors --all --dry-run

Environment (reads from .env or environment):
    BACKEND_OPENAI_API_KEY     required
    BACKEND_EMBEDDING_MODEL    default: text-embedding-3-small
    BACKEND_VECTOR_STORE_TYPE  default: chroma
    BACKEND_CHROMA_PERSIST_DIR default: data/chroma
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Allow running from the backend directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("ingest_vectors")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _build_entity_text(entity: dict, entity_type: str) -> str:
    """
    Build a rich text blob for a single entity.

    The text is designed to be semantically dense so that vibe queries
    ("trendy date night", "something romantic", "gift for mum") map to
    the right entities via cosine similarity.
    """
    parts: list[str] = []

    name = entity.get("name") or entity.get("title", "")
    if name:
        parts.append(name)

    category = entity.get("category", "")
    subcategory = entity.get("subcategory", "")
    if category:
        parts.append(category)
    if subcategory and subcategory != category:
        parts.append(subcategory)

    # Dining-specific
    dining_style = entity.get("dining_style", "")
    cuisine_type = entity.get("cuisine_type", "")
    if dining_style:
        parts.append(dining_style.replace("_", " "))
    if cuisine_type:
        parts.append(cuisine_type)

    # Tags — the most semantically rich field
    tags = entity.get("tags", [])
    if tags:
        parts.extend(t.replace("_", " ") for t in tags[:12])

    # Description
    desc = entity.get("description", "")
    if desc:
        parts.append(desc[:200])

    entity_type_label = entity_type.rstrip("s")  # "stores" → "store"
    parts.append(entity_type_label)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        lp = p.lower().strip()
        if lp and lp not in seen:
            seen.add(lp)
            unique.append(p.strip())

    return ", ".join(unique)


def _load_canonical(mall_id: str) -> dict:
    path = DATA_DIR / "canonical" / f"{mall_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Canonical data not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_entities(canonical: dict, mall_id: str) -> list[dict]:
    """Extract all embeddable entities from the canonical JSON."""
    entity_sections = {
        "stores": "store",
        "dining": "dining",
        "cinemas": "cinema",
        "services": "service",
    }
    entities: list[dict] = []

    for section, etype in entity_sections.items():
        for raw in canonical.get(section, []):
            entity_id = raw.get("entity_id") or raw.get("id", "")
            if not entity_id:
                continue
            text = _build_entity_text(raw, section)
            if not text.strip():
                continue
            entities.append({
                "entity_id": entity_id,
                "entity_type": etype,
                "text": text,
                "mall_id": mall_id,
            })

    # Events and offers (optional — embed if present)
    for raw in canonical.get("events", []):
        entity_id = raw.get("entity_id") or raw.get("id", "")
        if not entity_id:
            continue
        title = raw.get("title", "")
        desc = raw.get("description", "")
        tags = raw.get("tags", [])
        text = ", ".join(filter(None, [title, desc[:150]] + tags))
        if text.strip():
            entities.append({
                "entity_id": entity_id,
                "entity_type": "event",
                "text": text,
                "mall_id": mall_id,
            })

    return entities


async def ingest_mall(mall_id: str, dry_run: bool = False) -> int:
    """
    Embed and upsert all entities for a single mall.

    Returns the number of vectors upserted (or that would be upserted in dry-run).
    """
    logger.info("Processing mall: %s", mall_id)

    try:
        canonical = _load_canonical(mall_id)
    except FileNotFoundError as exc:
        logger.error("%s — skipping", exc)
        return 0

    entities = _extract_entities(canonical, mall_id)
    logger.info("  Found %d entities to embed", len(entities))

    if dry_run:
        for e in entities[:5]:
            logger.info("  [DRY-RUN] %s (%s): %s", e["entity_id"], e["entity_type"], e["text"][:80])
        if len(entities) > 5:
            logger.info("  [DRY-RUN] ... and %d more", len(entities) - 5)
        return len(entities)

    # Import settings and build the service
    from app.config.settings import get_settings
    from app.services.vector_store import VectorStoreService

    settings = get_settings()
    vs = VectorStoreService(settings)
    await vs.connect()

    if not vs.is_available:
        logger.error("VectorStoreService not available — check OPENAI_API_KEY and chromadb installation")
        return 0

    count = await vs.upsert(entities, mall_id)
    await vs.close()
    logger.info("  Upserted %d vectors for mall %s", count, mall_id)
    return count


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed mall entities and upsert into Chroma vector store",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mall-id", help="Single mall ID to ingest (e.g. al_nakheel_plaza_28)")
    group.add_argument("--all", action="store_true", help="Ingest all malls in data/canonical/")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be ingested without embedding")
    args = parser.parse_args()

    if args.all:
        canonical_dir = DATA_DIR / "canonical"
        if not canonical_dir.exists():
            logger.error("Canonical data directory not found: %s", canonical_dir)
            sys.exit(1)
        mall_ids = [p.stem for p in sorted(canonical_dir.glob("*.json"))]
        if not mall_ids:
            logger.error("No canonical JSON files found in %s", canonical_dir)
            sys.exit(1)
        logger.info("Found %d malls: %s", len(mall_ids), mall_ids)
    else:
        mall_ids = [args.mall_id]

    total = 0
    for mall_id in mall_ids:
        count = await ingest_mall(mall_id, dry_run=args.dry_run)
        total += count

    logger.info("Done. Total vectors %s: %d", "that would be upserted" if args.dry_run else "upserted", total)


if __name__ == "__main__":
    asyncio.run(main())
