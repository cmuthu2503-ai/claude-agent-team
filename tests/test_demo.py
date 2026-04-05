"""Demo environment tests — validates seed data and critical paths."""

import pytest
from pathlib import Path


def test_seed_script_exists():
    assert Path("demo/seed.py").exists()


def test_seed_data_files_exist():
    assert Path("demo/seed-data/users.json").exists()
    assert Path("demo/seed-data/sample-data.json").exists()


def test_seed_data_valid_json():
    import json
    with open("demo/seed-data/users.json") as f:
        users = json.load(f)
    assert len(users) == 3
    assert users[0]["username"] == "admin"

    with open("demo/seed-data/sample-data.json") as f:
        data = json.load(f)
    assert len(data["requests"]) == 5


async def test_seed_creates_data(tmp_path):
    """Test that the seed script populates the database."""
    import sys
    sys.path.insert(0, ".")
    from demo.seed import seed
    from src.state.sqlite_store import SQLiteStateStore

    db_path = str(tmp_path / "demo.db")
    await seed(db_path)

    store = SQLiteStateStore(db_path=db_path)
    await store.initialize()

    requests = await store.list_requests(limit=10)
    assert len(requests) == 5

    users = await store.list_users()
    assert len(users) == 3

    stories = await store.get_stories_for_request("REQ-001")
    assert len(stories) == 4

    deployments = await store.list_deployments(limit=5)
    assert len(deployments) == 1

    await store.close()


async def test_seed_is_idempotent(tmp_path):
    """Running seed twice should not duplicate data."""
    from demo.seed import seed
    from src.state.sqlite_store import SQLiteStateStore

    db_path = str(tmp_path / "demo2.db")
    await seed(db_path)
    await seed(db_path)  # second run

    store = SQLiteStateStore(db_path=db_path)
    await store.initialize()
    requests = await store.list_requests(limit=20)
    assert len(requests) == 5  # not 10
    await store.close()
