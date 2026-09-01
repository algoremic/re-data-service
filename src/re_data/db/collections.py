"""MongoDB collection name helpers for re-data-service (optional future use)."""

from __future__ import annotations

from dataclasses import dataclass

from re_data.config import Settings


@dataclass(frozen=True)
class MongoCollections:
    ready: str
    off_plan: str


def get_collections(settings: Settings) -> MongoCollections:
    return MongoCollections(
        ready=settings.mongo_collection_ready,
        off_plan=settings.mongo_collection_offplan,
    )
