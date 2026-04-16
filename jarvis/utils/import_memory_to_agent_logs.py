"""Import jarvis_memory.json entries into MongoDB agent_logs collection."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from pymongo import MongoClient


def import_memory(
    memory_path: Path,
    mongo_uri: str = "mongodb://localhost:27017",
    db_name: str = "jarvis",
    collection_name: str = "agent_logs",
) -> dict[str, int]:
    if not memory_path.exists():
        raise FileNotFoundError(f"Memory file not found: {memory_path}")

    data = json.loads(memory_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Memory JSON must be a list of messages")

    client = MongoClient(mongo_uri)
    col = client[db_name][collection_name]

    inserted = 0
    skipped = 0
    source = str(memory_path.name)

    for idx, msg in enumerate(data):
        if not isinstance(msg, dict):
            skipped += 1
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            skipped += 1
            continue

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        dedupe_key = f"{source}:{idx}:{role}:{content_hash}"
        doc = {
            "agent_name": "memory_import",
            "type": "memory_message",
            "task_id": dedupe_key,
            "task_description": f"Imported {role} message from {source}",
            "role": role,
            "content": content,
            "message_index": idx,
            "source_file": source,
            "content_hash": content_hash,
            "success": True,
            "result_summary": content[:1000],
            "timestamp": time.time(),
        }
        res = col.update_one({"task_id": dedupe_key}, {"$setOnInsert": doc}, upsert=True)
        if res.upserted_id is not None:
            inserted += 1
        else:
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "total": len(data)}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    memory_file = root / "jarvis_memory.json"
    out = import_memory(memory_file)
    print(
        f"Import complete: inserted={out['inserted']} skipped={out['skipped']} total={out['total']}"
    )
