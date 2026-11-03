"""Registry storage with a versioned schema.

Two backends share one interface:
  * SQLiteStore   - local default
  * DynamoStore   - AWS DynamoDB (tested locally with moto)

Every record carries `schema_version`. Records written by an older version
are upgraded on read by `migrate`, so old data keeps working.
"""
import json
import sqlite3
import time

SCHEMA_VERSION = 2


def migrate(record: dict) -> dict:
    """Upgrade any older record to the current schema. Never raises on old data.

    v1: {"name", "config", "tool_count"}               (no tool names)
    v2: {"name", "config", "tools": [...], "updated_at", "schema_version": 2}
    """
    rec = dict(record)
    version = int(rec.get("schema_version", 1))
    if version < 2:
        rec.setdefault("tools", [])
        rec.setdefault("updated_at", 0)
        rec.pop("tool_count", None)
    rec["schema_version"] = SCHEMA_VERSION
    return rec


def make_record(name: str, config: dict, tools: list[str]) -> dict:
    safe_config = {k: v for k, v in config.items() if k != "env"}  # never store secrets
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "config": safe_config,
        "tools": sorted(tools),
        "updated_at": int(time.time()),
    }


class SQLiteStore:
    def __init__(self, path: str = "registry.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS servers (name TEXT PRIMARY KEY, record TEXT NOT NULL)"
        )
        self.conn.commit()

    def put(self, record: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO servers (name, record) VALUES (?, ?)",
            (record["name"], json.dumps(record)),
        )
        self.conn.commit()

    def get(self, name: str):
        row = self.conn.execute("SELECT record FROM servers WHERE name = ?", (name,)).fetchone()
        return migrate(json.loads(row[0])) if row else None

    def delete(self, name: str):
        self.conn.execute("DELETE FROM servers WHERE name = ?", (name,))
        self.conn.commit()

    def all(self) -> list[dict]:
        rows = self.conn.execute("SELECT record FROM servers ORDER BY name").fetchall()
        return [migrate(json.loads(r[0])) for r in rows]


class DynamoStore:
    """DynamoDB table with partition key `name`. Records are stored as JSON strings."""

    def __init__(self, table_name: str, client=None):
        import boto3

        self.table = table_name
        self.client = client or boto3.client("dynamodb")

    def put(self, record: dict):
        self.client.put_item(
            TableName=self.table,
            Item={"name": {"S": record["name"]}, "record": {"S": json.dumps(record)}},
        )

    def get(self, name: str):
        res = self.client.get_item(TableName=self.table, Key={"name": {"S": name}})
        item = res.get("Item")
        return migrate(json.loads(item["record"]["S"])) if item else None

    def delete(self, name: str):
        self.client.delete_item(TableName=self.table, Key={"name": {"S": name}})

    def all(self) -> list[dict]:
        items = self.client.scan(TableName=self.table).get("Items", [])
        return sorted((migrate(json.loads(i["record"]["S"])) for i in items), key=lambda r: r["name"])


def store_from_env():
    """DYNAMODB_TABLE set -> DynamoDB, otherwise local SQLite."""
    import os

    table = os.environ.get("DYNAMODB_TABLE")
    return DynamoStore(table) if table else SQLiteStore(os.environ.get("REGISTRY_DB", "registry.db"))
