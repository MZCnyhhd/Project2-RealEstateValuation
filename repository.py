import os
import json
import sqlite3


class JsonRepository:
    def __init__(self, filename="mock_listings.json"):
        self.filename = filename

    def list_listings(self):
        with open(self.filename, "r", encoding="utf-8") as f:
            return json.load(f)


class SqliteRepository:
    def __init__(self, db_path="realestate.db"):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    community TEXT,
                    address TEXT,
                    layout TEXT,
                    area REAL,
                    price REAL,
                    tags TEXT,
                    description TEXT,
                    image_url TEXT,
                    latitude REAL,
                    longitude REAL
                )
                """
            )

    def list_listings(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM listings ORDER BY id").fetchall()
            listings = []
            for r in rows:
                item = dict(r)
                tags = item.get("tags")
                if tags:
                    try:
                        item["tags"] = json.loads(tags)
                    except Exception:
                        item["tags"] = []
                else:
                    item["tags"] = []
                listings.append(item)
            return listings

    def import_from_json(self, filename="mock_listings.json"):
        with open(filename, "r", encoding="utf-8") as f:
            listings = json.load(f)

        with self._connect() as conn:
            for item in listings:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO listings
                    (id, title, community, address, layout, area, price, tags, description, image_url, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("id"),
                        item.get("title"),
                        item.get("community"),
                        item.get("address"),
                        item.get("layout"),
                        item.get("area"),
                        item.get("price"),
                        json.dumps(item.get("tags", []), ensure_ascii=False),
                        item.get("description"),
                        item.get("image_url"),
                        item.get("latitude"),
                        item.get("longitude"),
                    ),
                )


def get_repository(filename="mock_listings.json"):
    backend = os.environ.get("DATA_BACKEND", "json").strip().lower()
    if backend == "sqlite":
        db_path = os.environ.get("SQLITE_PATH", "realestate.db")
        repo = SqliteRepository(db_path=db_path)
        if os.environ.get("SQLITE_AUTO_IMPORT", "0") == "1":
            repo.import_from_json(filename=filename)
        return repo

    return JsonRepository(filename=filename)
