#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from qdrant_client import QdrantClient, models


def is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def wait_for_qdrant(client: QdrantClient, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            client.get_collections()
            return
        except Exception as exc:  # noqa: BLE001 - startup wait should report the original client error.
            last_error = exc
            print(f"Waiting for Qdrant HTTP API: {exc}", flush=True)
            time.sleep(2)
    raise RuntimeError(f"Qdrant HTTP API was not ready within {timeout_seconds}s: {last_error}")


def recreate_collection(source: QdrantClient, dest: QdrantClient, collection_name: str) -> None:
    info = source.get_collection(collection_name)
    params = info.config.params
    dest.create_collection(
        collection_name=collection_name,
        vectors_config=params.vectors,
        sparse_vectors_config=params.sparse_vectors,
        shard_number=params.shard_number,
        sharding_method=params.sharding_method,
        replication_factor=params.replication_factor,
        write_consistency_factor=params.write_consistency_factor,
        on_disk_payload=params.on_disk_payload,
        timeout=120,
    )


def copy_points(source: QdrantClient, dest: QdrantClient, collection_name: str, expected_count: int, batch_size: int) -> int:
    copied = 0
    offset = None
    while True:
        points, offset = source.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            break
        dest.upsert(
            collection_name=collection_name,
            points=[
                models.PointStruct(id=point.id, vector=point.vector, payload=point.payload or {})
                for point in points
            ],
            wait=True,
        )
        copied += len(points)
        if copied == expected_count or copied % 1000 < len(points):
            print(f"Seeded Qdrant points: {copied}/{expected_count}", flush=True)
        if offset is None:
            break
    return copied


def main() -> int:
    source_path = Path(os.environ.get("QDRANT_SEED_SOURCE", "/seed/qdrant"))
    collection_name = os.environ.get("QDRANT_COLLECTION", "datacenter_chunks_v1")
    qdrant_url = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    api_key_env = os.environ.get("QDRANT_API_KEY_ENV", "QDRANT_API_KEY")
    api_key = os.environ.get(api_key_env) or None
    force = is_true(os.environ.get("GONGKAN_KNOWLEDGE_SEED_FORCE"))
    batch_size = int(os.environ.get("QDRANT_SEED_BATCH_SIZE", "128"))
    wait_timeout = int(os.environ.get("QDRANT_SEED_WAIT_SECONDS", "180"))

    if not source_path.exists():
        raise RuntimeError(f"Qdrant seed source does not exist: {source_path}")

    source = QdrantClient(path=str(source_path), timeout=120)
    try:
        dest = QdrantClient(
            url=qdrant_url,
            api_key=api_key,
            prefer_grpc=False,
            timeout=120,
            check_compatibility=False,
        )
    except TypeError:
        dest = QdrantClient(url=qdrant_url, api_key=api_key, prefer_grpc=False, timeout=120)
    try:
        wait_for_qdrant(dest, wait_timeout)
        expected_count = int(source.count(collection_name=collection_name, exact=True).count)
        if expected_count <= 0:
            raise RuntimeError(f"Qdrant seed source collection is empty: {collection_name}")

        exists = dest.collection_exists(collection_name)
        if force and exists:
            print(f"GONGKAN_KNOWLEDGE_SEED_FORCE=true, deleting HTTP collection {collection_name}", flush=True)
            dest.delete_collection(collection_name=collection_name, timeout=120)
            exists = False

        if exists:
            actual_count = int(dest.count(collection_name=collection_name, exact=True).count)
            if actual_count == expected_count:
                print(f"Qdrant HTTP collection {collection_name} already seeded: {actual_count} points", flush=True)
                return 0
            if actual_count > expected_count:
                print(
                    f"Qdrant HTTP collection {collection_name} has {actual_count} points "
                    f"(bundled seed has {expected_count}); keeping existing collection",
                    flush=True,
                )
                return 0
            print(
                f"Qdrant HTTP collection {collection_name} is incomplete: {actual_count}/{expected_count}; rebuilding",
                flush=True,
            )
            dest.delete_collection(collection_name=collection_name, timeout=120)

        recreate_collection(source, dest, collection_name)
        copied = copy_points(source, dest, collection_name, expected_count, batch_size)
        actual_count = int(dest.count(collection_name=collection_name, exact=True).count)
        if copied != expected_count or actual_count < expected_count:
            raise RuntimeError(
                f"Qdrant HTTP seed incomplete: copied={copied}, actual={actual_count}, expected={expected_count}"
            )
        print(f"Seeded Qdrant HTTP collection {collection_name}: {actual_count} points", flush=True)
        return 0
    finally:
        source.close()
        dest.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - container logs should show the seed failure clearly.
        print(f"Qdrant HTTP seed failed: {exc}", file=sys.stderr, flush=True)
        raise
