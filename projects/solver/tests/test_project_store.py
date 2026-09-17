from pathlib import Path

from backend.core.project_store import ProjectStore


def test_save_and_load_round_trip(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects.db")
    payload = {"title": "示例", "objective_value": 2360}
    created = store.save("测试", payload)
    assert created["version"] == 1

    loaded = store.get(created["id"])
    assert loaded is not None
    assert loaded["name"] == "测试"
    assert loaded["payload"] == payload
    assert len(loaded["versions"]) == 1


def test_versions_increment(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects.db")
    first = store.save("项目", {"v": 1})
    second = store.save("项目", {"v": 2}, first["id"])
    third = store.save("项目", {"v": 3}, first["id"])

    assert (first["version"], second["version"], third["version"]) == (1, 2, 3)

    latest = store.get(first["id"])
    assert latest["payload"]["v"] == 3
    assert [item["version"] for item in latest["versions"]] == [3, 2, 1]


def test_get_specific_version(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects.db")
    project = store.save("案例", {"v": "old"})
    store.save("案例", {"v": "new"}, project["id"])

    v1 = store.get(project["id"], version=1)
    assert v1["payload"]["v"] == "old"
    assert v1["version"] == 1


def test_get_missing_returns_none(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects.db")
    assert store.get("does-not-exist") is None
