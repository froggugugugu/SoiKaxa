import json

import pytest
from soikaxa.upgrade import STALE_SEC, UpgradeError, Upgrader, parse_version


def make(tmp_path, release=None, started=None, now=1000.0, current="0.1.0"):
    calls = started if started is not None else []

    def start():
        calls.append(1)
        return True

    up = Upgrader(
        data_dir=tmp_path,
        start_unit=start,
        fetch_latest=lambda: release,
        current=current,
        status_dir=tmp_path / "root",
        clock=lambda: now,
    )
    return up, calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v0.2.0", (0, 2, 0)),
        ("0.2.0", (0, 2, 0)),
        ("v0.2", None),  # 2 桁は upgrade.sh が受け付けないので揃える
        ("v1.10.3", (1, 10, 3)),
        ("latest", None),
        ("v1.2.3-rc1", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def test_check_reports_newer_release(tmp_path):
    up, _ = make(tmp_path, {"tag_name": "v0.10.0", "body": "変更点"})
    assert up.check() == {"current": "0.1.0", "latest": "0.10.0", "available": True, "notes": "変更点"}


@pytest.mark.parametrize(
    "release", [None, {"tag_name": "v0.1.0"}, {"tag_name": "v0.0.9"}, {"tag_name": "nightly"}]
)
def test_check_not_available(tmp_path, release):
    up, _ = make(tmp_path, release)
    assert up.check()["available"] is False


def test_start_runs_unit_and_records_request(tmp_path):
    up, calls = make(tmp_path, {"tag_name": "v0.2.0"})
    assert up.start() is True and calls == [1]
    st = up.status()
    assert st["state"] == "requested" and st["from"] == "0.1.0" and st["at"] == 1000
    # 実行中は二重に起動しない
    assert up.start() is False and calls == [1]


def test_start_refuses_when_up_to_date(tmp_path):
    up, calls = make(tmp_path, {"tag_name": "v0.1.0"})
    assert up.start() is False and calls == []
    assert up.status() == {"state": "idle"}


def test_start_unit_failure_is_recorded(tmp_path):
    up = Upgrader(
        tmp_path,
        start_unit=lambda: False,
        fetch_latest=lambda: {"tag_name": "v0.2.0"},
        current="0.1.0",
        status_dir=tmp_path,
    )
    assert up.start() is False
    assert up.status()["state"] == "failed"


def test_stale_running_status_is_reported_as_failed(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "upgrade.json").write_text(json.dumps({"state": "running", "at": 0}))
    up, calls = make(tmp_path, {"tag_name": "v0.2.0"}, now=STALE_SEC + 1)
    assert up.status()["state"] == "failed" and not up.busy()
    assert up.start() is True and calls == [1]


def test_status_ignores_broken_file(tmp_path):
    (tmp_path / "upgrade-request.json").write_text("{broken")
    up, _ = make(tmp_path)
    assert up.status() == {"state": "idle"}


def test_network_error_propagates(tmp_path):
    def boom():
        raise UpgradeError("インターネットに接続できません")

    up = Upgrader(tmp_path, start_unit=lambda: True, fetch_latest=boom, current="0.1.0", status_dir=tmp_path)
    with pytest.raises(UpgradeError):
        up.check()


def test_status_prefers_newer_of_request_and_root_result(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    up, _ = make(tmp_path, {"tag_name": "v0.2.0"})
    assert up.start() is True  # 依頼（at=1000）はデーモンのデータ領域に書く
    assert not (root / "upgrade.json").exists()
    (root / "upgrade.json").write_text(json.dumps({"state": "done", "message": "古い結果", "at": 500}))
    assert up.status()["state"] == "requested"
    (root / "upgrade.json").write_text(
        json.dumps({"state": "running", "message": "入れています", "at": 1000})
    )
    assert up.status()["message"] == "入れています"  # 同じ秒なら root 側
    (root / "upgrade.json").write_text(json.dumps({"state": "done", "message": "完了", "at": 1001}))
    assert up.status()["state"] == "done" and not up.busy()
