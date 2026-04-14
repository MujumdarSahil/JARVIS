"""
MongoDB connection manager (singleton). Jarvis continues without MongoDB if unavailable.
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

try:
    from pymongo import ASCENDING, DESCENDING, MongoClient
    from pymongo.errors import PyMongoError

    _PYMONGO = True
except ImportError:
    MongoClient = None  # type: ignore[misc, assignment]
    PyMongoError = Exception  # type: ignore[misc, assignment]
    ASCENDING = 1
    DESCENDING = -1
    _PYMONGO = False


class Database:
    """
    Singleton MongoDB manager. Call ``connect()`` after loading config.
    All methods are defensive: failures log and return safe defaults.
    """

    _instance: Database | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Database:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_once(*args, **kwargs)
        return cls._instance

    def _init_once(
        self,
        uri: str = "mongodb://localhost:27017",
        db_name: str = "jarvis",
    ) -> None:
        self._uri = uri
        self._db_name = db_name
        self._client: Any = None
        self._db: Any = None
        self.available: bool = False

    def connect(self, uri: str | None = None, db_name: str | None = None) -> bool:
        """Connect, ping, create indexes. On failure: log warning, ``available=False``."""
        if uri:
            self._uri = uri
        if db_name:
            self._db_name = db_name

        if not _PYMONGO:
            logger.warning("pymongo not installed — MongoDB disabled.")
            self.available = False
            return False

        try:
            self._client = MongoClient(
                self._uri,
                serverSelectionTimeoutMS=3000,
                connectTimeoutMS=3000,
                socketTimeoutMS=5000,
            )
            self._client.admin.command("ping")
            self._db = self._client[self._db_name]
            self._ensure_indexes()
            self.available = True
            return True
        except Exception as e:
            logger.warning("MongoDB unavailable: %s", e)
            print(f"[DB] MongoDB unavailable — reason: {type(e).__name__}: {e}")
            self._client = None
            self._db = None
            self.available = False
            return False

    def _ensure_indexes(self) -> None:
        if self._db is None:
            return
        try:
            conv = self._db["conversations"]
            conv.create_index([("session_id", ASCENDING)])
            conv.create_index([("timestamp", DESCENDING)])
            conv.create_index([("content", "text")])

            mem = self._db["memories"]
            mem.create_index([("user_id", ASCENDING)])
            mem.create_index([("tags", ASCENDING)])
            mem.create_index([("importance_score", DESCENDING)])
            mem.create_index([("content", "text")])

            logs = self._db["agent_logs"]
            logs.create_index([("agent_name", ASCENDING)])
            logs.create_index([("timestamp", DESCENDING)])
            logs.create_index([("task_id", ASCENDING)])

            prof = self._db["user_profile"]
            prof.create_index([("user_id", ASCENDING)], unique=False)

            emb = self._db["embeddings"]
            emb.create_index([("text_hash", ASCENDING)])
            emb.create_index([("collection_ref", ASCENDING)])
        except Exception as e:
            logger.error("Failed to create MongoDB indexes: %s", e)

    def get_collection(self, name: str) -> Any:
        """Return collection handle (creates on first write). Empty stub if unavailable."""
        if not self.available or self._db is None:
            return None
        try:
            return self._db[name]
        except Exception as e:
            logger.error("get_collection(%s) failed: %s", name, e)
            return None

    def insert(self, collection: str, doc: dict) -> str:
        try:
            if not self.available:
                return ""
            col = self.get_collection(collection)
            if col is None:
                return ""
            out = col.insert_one(dict(doc))
            return str(out.inserted_id)
        except Exception as e:
            logger.error("db.insert(%s) failed: %s", collection, e)
            return ""

    def find(
        self,
        collection: str,
        query: dict,
        limit: int = 50,
        sort: list | None = None,
    ) -> list:
        try:
            if not self.available:
                return []
            col = self.get_collection(collection)
            if col is None:
                return []
            cur = col.find(dict(query))
            if sort:
                cur = cur.sort(sort)
            return list(cur.limit(max(1, min(limit, 500))))
        except Exception as e:
            logger.error("db.find(%s) failed: %s", collection, e)
            return []

    def find_one(self, collection: str, query: dict) -> dict | None:
        try:
            if not self.available:
                return None
            col = self.get_collection(collection)
            if col is None:
                return None
            doc = col.find_one(dict(query))
            return dict(doc) if doc else None
        except Exception as e:
            logger.error("db.find_one(%s) failed: %s", collection, e)
            return None

    def update(
        self,
        collection: str,
        query: dict,
        update: dict,
        upsert: bool = False,
    ) -> bool:
        try:
            if not self.available:
                return False
            col = self.get_collection(collection)
            if col is None:
                return False
            upd = dict(update)
            if upd and not any(k.startswith("$") for k in upd):
                upd = {"$set": upd}
            res = col.update_one(dict(query), upd, upsert=upsert)
            return bool(res.modified_count or res.upserted_id or (upsert and res.matched_count))
        except Exception as e:
            logger.error("db.update(%s) failed: %s", collection, e)
            return False

    def delete(self, collection: str, query: dict) -> int:
        try:
            if not self.available:
                return 0
            col = self.get_collection(collection)
            if col is None:
                return 0
            res = col.delete_many(dict(query))
            return int(res.deleted_count)
        except Exception as e:
            logger.error("db.delete(%s) failed: %s", collection, e)
            return 0

    def text_search(self, collection: str, search_str: str, limit: int = 10) -> list:
        try:
            if not self.available or not (search_str or "").strip():
                return []
            col = self.get_collection(collection)
            if col is None:
                return []
            cur = col.find({"$text": {"$search": search_str}}).limit(max(1, min(limit, 50)))
            return list(cur)
        except Exception as e:
            logger.error("db.text_search(%s) failed: %s", collection, e)
            return []

    def count(self, collection: str, query: dict | None = None) -> int:
        try:
            if not self.available:
                return 0
            col = self.get_collection(collection)
            if col is None:
                return 0
            return int(col.count_documents(dict(query or {})))
        except Exception as e:
            logger.error("db.count(%s) failed: %s", collection, e)
            return 0


db = Database()


if __name__ == "__main__":
    result = db.connect()
    print(f"Connected: {result}, Available: {db.available}")
