"""Registry store: SQLite + DynamoDB (moto), schema versioning, backward compatibility."""
import json

import boto3
import pytest
from moto import mock_aws

from aggregator.store import SCHEMA_VERSION, DynamoStore, SQLiteStore, make_record, migrate

pytestmark = pytest.mark.anyio


def _rec(name="demo"):
    return make_record(name, {"command": "python", "args": ["x.py"], "env": {"K": "SECRET"}}, ["demo__b", "demo__a"])


def test_record_never_stores_env_secrets():
    assert "env" not in _rec()["config"]


def test_sqlite_roundtrip(tmp_path):
    s = SQLiteStore(str(tmp_path / "r.db"))
    s.put(_rec())
    got = s.get("demo")
    assert got["tools"] == ["demo__a", "demo__b"] and got["schema_version"] == SCHEMA_VERSION
    s.delete("demo")
    assert s.get("demo") is None


def test_backward_compat_v1_record_still_readable(tmp_path):
    """A record written by schema v1 must load under the current code."""
    v1 = {"name": "legacy", "config": {"command": "python"}, "tool_count": 4}   # no schema_version
    s = SQLiteStore(str(tmp_path / "r.db"))
    s.conn.execute("INSERT INTO servers VALUES (?, ?)", ("legacy", json.dumps(v1)))
    s.conn.commit()
    got = s.get("legacy")
    assert got["schema_version"] == SCHEMA_VERSION
    assert got["tools"] == [] and "tool_count" not in got
    assert got["config"] == {"command": "python"}          # original data preserved


def test_migrate_is_idempotent_and_keeps_current_records():
    cur = _rec()
    assert migrate(cur) == cur
    assert migrate(migrate({"name": "x", "config": {}})) == migrate({"name": "x", "config": {}})


def test_current_schema_fields_are_a_superset_of_v1():
    """Contract: new versions may add fields but must never drop v1 ones."""
    assert {"name", "config"} <= set(_rec())


@mock_aws
def test_dynamodb_roundtrip_and_v1_compat():
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName="reg", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}],
    )
    s = DynamoStore("reg", client=client)
    s.put(_rec("a"))
    client.put_item(TableName="reg", Item={"name": {"S": "old"}, "record": {"S": json.dumps({"name": "old", "config": {}, "tool_count": 2})}})
    assert [r["name"] for r in s.all()] == ["a", "old"]
    assert s.get("old")["schema_version"] == SCHEMA_VERSION
    s.delete("a")
    assert s.get("a") is None


async def test_manager_persists_registry(make_config, monkeypatch, tmp_path):
    from aggregator.upstream import UpstreamManager
    monkeypatch.setenv("NOTES_TOKEN", "t")
    store = SQLiteStore(str(tmp_path / "r.db"))
    m = UpstreamManager(make_config(), store=store)
    await m.start()
    try:
        assert "demo__add" in store.get("demo")["tools"]
        await m.remove_server("demo")
        assert store.get("demo") is None
    finally:
        await m.close()
