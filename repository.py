import os
import json
import sqlite3
import threading

_JSON_LOCK = threading.Lock()


class JsonRepository:
    def __init__(self, filename="mock_listings.json"):
        self.filename = filename

    def list_listings(self):
        with open(self.filename, "r", encoding="utf-8") as f:
            return json.load(f)

    def add_listing(self, listing):
        """新增房源并置顶（用于把用户估值房源回流到 Demo 房源）。"""
        with _JSON_LOCK:
            with open(self.filename, "r", encoding="utf-8") as f:
                data = json.load(f)
            data = [item for item in data if item.get("id") != listing.get("id")]
            data.insert(0, listing)
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        return listing


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

    def add_listing(self, listing):
        """新增/覆盖一条房源（用于把用户估值房源回流到 Demo 房源）。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO listings
                (id, title, community, address, layout, area, price, tags, description, image_url, latitude, longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.get("id"),
                    listing.get("title"),
                    listing.get("community"),
                    listing.get("address"),
                    listing.get("layout"),
                    listing.get("area"),
                    listing.get("price"),
                    json.dumps(listing.get("tags", []), ensure_ascii=False),
                    listing.get("description"),
                    listing.get("image_url"),
                    listing.get("latitude"),
                    listing.get("longitude"),
                ),
            )
        return listing

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


class OrderStore:
    """服务端订单存储 —— 供微信/支付宝支付回调按 out_trade_no 查询订单。

    注意：当前用 JSON 文件实现（与 DATA_BACKEND=json 一致）。
    Render 等临时文件系统会在重启时清空，但支付回调通常在用户付款后数秒内到达，
    足以完成一次交易闭环；如需持久化请改用 SQLite 或外部数据库。
    """

    def __init__(self, path="orders.json"):
        self.path = path
        self._lock = threading.Lock()

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def save(self, order):
        with self._lock:
            data = self._load()
            data[order["order_no"]] = order
            self._save(data)

    def get(self, order_no):
        with self._lock:
            return self._load().get(order_no)

    def all(self):
        with self._lock:
            return self._load()

    def update(self, order_no, **fields):
        with self._lock:
            data = self._load()
            o = data.get(order_no)
            if not o:
                return None
            o.update(fields)
            data[order_no] = o
            self._save(data)
            return o


_order_store = None


def get_order_store():
    global _order_store
    if _order_store is None:
        _order_store = OrderStore(path=os.environ.get("ORDERS_PATH", "orders.json"))
    return _order_store

