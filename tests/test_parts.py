"""Sessions and health data as two parts, each one off or on, and catching up when switched on later."""
import csv
import datetime as dt
import json

import pytest

from training_export import exporter, health, settings
from fakes import FakeGarmin, activity, health_replies

NOW = dt.datetime(2026, 11, 14, 7, 12)


def newest_health(root):
    f = sorted((root / exporter.HEALTH_DIR).glob("health-*.csv"), key=lambda p: p.name[-31:])[-1]
    return f.name, {r[1] for r in list(csv.reader(f.open(encoding="utf-8")))[1:]}


def test_sessions_off_downloads_no_files_and_lists_nothing(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(dt.date(2026, 11, 13)))
    calls = []
    fake.activities = lambda a, b: calls.append((a, b)) or []
    r = exporter.refresh(fake, tmp_path, health.ALL_KINDS, 1, sessions=False, now=NOW)
    assert r["new_sessions"] == 0 and calls == [] and fake.downloads == []
    assert not (tmp_path / exporter.ACT_DIR).exists()
    assert list((tmp_path / exporter.HEALTH_DIR).glob("health-*.csv"))


def test_health_off_writes_no_csv_and_asks_garmin_nothing_about_health(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(dt.date(2026, 11, 13)))
    exporter.refresh(fake, tmp_path, [], 1, now=NOW)
    assert fake.paths == []
    assert not (tmp_path / exporter.HEALTH_DIR).exists()


def test_sessions_switched_on_later_reach_back_to_the_first_export_start(tmp_path):
    acts = [activity(1, "2026-10-20 07:00:00"), activity(2, "2026-11-20 07:00:00")]
    exporter.refresh(FakeGarmin(acts), tmp_path, ["hrv"], 1, sessions=False, now=NOW)     # starts 2026-10-14
    fake = FakeGarmin(acts)
    exporter.refresh(fake, tmp_path, ["hrv"], 24, sessions=True, now=dt.datetime(2026, 11, 21, 7, 0))
    assert fake.downloads == [1, 2]          # back to 2026-10-14, not 24 months: first_months only counts once


def test_a_health_kind_switched_on_later_catches_up_alone(tmp_path):
    day = dt.date(2026, 10, 20)
    exporter.refresh(FakeGarmin([], health_replies(day)), tmp_path, ["hrv"], 1, now=NOW)
    later = dt.datetime(2026, 11, 21, 7, 0)
    exporter.refresh(FakeGarmin([], health_replies(day)), tmp_path, ["hrv", "weight"], 1, now=later)
    name, types = newest_health(tmp_path)
    assert name == "health-2026-10-14-2026-11-21 0700.csv"     # weight from the first start; hrv rides along
    assert types == {"HRV Night", "Weight Kilograms"}
    exporter.refresh(FakeGarmin([], health_replies(day)), tmp_path, ["hrv", "weight"], 1,
                     now=dt.datetime(2026, 11, 28, 7, 0))
    assert newest_health(tmp_path)[0] == "health-2026-11-14-2026-11-28 0700.csv"


def test_state_from_version_0_1_0_carries_on(tmp_path):
    """Jacob's real first export was made by v0.1.0: one health date, 'All activities', no first_from."""
    old = tmp_path / "All activities"
    old.mkdir()
    (old / "2026-05-20 0700 running 1.fit").write_bytes(b"x")
    (tmp_path / exporter.STATE_FILE).write_text(json.dumps({
        "done_ids": [1], "failures": {}, "last_export": "2026-11-14T05:42:35", "activities_through": "2026-11-14",
        "health_through": "2026-11-14", "pending": None}))
    fake = FakeGarmin([activity(1, "2026-11-13 07:00:00"), activity(2, "2026-11-20 07:00:00")],
                      health_replies(dt.date(2026, 11, 20)))
    exporter.refresh(fake, tmp_path, health.ALL_KINDS, 24, now=dt.datetime(2026, 11, 21, 7, 0))
    assert fake.downloads == [2]
    assert (tmp_path / "Activities" / "2026-05-20 0700 running 1.fit").exists() and not old.exists()
    assert newest_health(tmp_path)[0] == "health-2026-11-07-2026-11-21 0700.csv"
    s = exporter.load_state(tmp_path)
    assert s["first_from"] == "2026-05-20" and set(s["health_through"]) == set(health.ALL_KINDS)


def test_nothing_to_download_is_refused():
    with pytest.raises(settings.Invalid):
        settings.clean(dict(settings.DEFAULTS, sessions=False, kinds=[]))


def test_the_default_folder_is_downloads():
    assert settings.DEFAULTS["folder"].endswith("Downloads\\Training Export") or \
        settings.DEFAULTS["folder"].endswith("Downloads/Training Export")
    f = settings.suggested_folders()
    assert set(f) == {"downloads", "desktop"} and all(v.endswith("Training Export") for v in f.values())


HEADER = '"Timestamp","Type","Value"' + chr(10)


def old_layout(root, all_name, pending=None):
    """What v0.1/v0.2 left: every .fit in the 'All' folder, and a dated 'New' folder per refresh holding that
    refresh's .fit copies and its health CSV (the only copy of the health data)."""
    (root / all_name).mkdir()
    for n in ("2026-05-20 0700 running 1.fit", "2026-11-13 0700 running 2.fit"):
        (root / all_name / n).write_bytes(b"fit " + n.encode())
    first, second = root / "New 2026-11-14 0542", root / "New 2026-11-15 0700"
    first.mkdir(), second.mkdir()
    (first / "2026-05-20 0700 running 1.fit").write_bytes(b"fit 2026-05-20 0700 running 1.fit")
    (first / "health-2026-05-18-2026-11-14.csv").write_text(HEADER)
    (second / "2026-11-13 0700 running 2.fit").write_bytes(b"fit 2026-11-13 0700 running 2.fit")
    (second / "health-2026-11-07-2026-11-15.csv").write_text(HEADER)
    (root / exporter.STATE_FILE).write_text(json.dumps({
        "done_ids": [1, 2], "failures": {}, "last_export": "2026-11-15T07:00:00", "activities_through": "2026-11-15",
        "first_from": "2026-05-18", "health_through": {k: "2026-11-15" for k in health.ALL_KINDS}, "pending": pending}))


@pytest.mark.parametrize("all_name", ["All activities", "All sessions"])
def test_the_old_layout_folds_into_activities_and_health(tmp_path, all_name):
    old_layout(tmp_path, all_name)
    fake = FakeGarmin([activity(3, "2026-11-20 07:00:00")], health_replies(dt.date(2026, 11, 20)))
    exporter.refresh(fake, tmp_path, health.ALL_KINDS, 24, now=dt.datetime(2026, 11, 21, 7, 0))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Activities", "Health", "export log.txt", "export-state.json"]
    assert sorted(p.name for p in (tmp_path / "Activities").iterdir()) == [
        "2026-05-20 0700 running 1.fit", "2026-11-13 0700 running 2.fit", "2026-11-20 0700 running 3.fit"]
    assert sorted(p.name for p in (tmp_path / "Health").iterdir()) == [
        "health-2026-05-18-2026-11-14.csv", "health-2026-11-07-2026-11-15.csv", "health-2026-11-08-2026-11-21 0700.csv"]
    assert fake.downloads == [3]


def test_folding_never_overwrites_a_file_that_differs(tmp_path):
    old_layout(tmp_path, "All sessions")
    (tmp_path / "New 2026-11-14 0542" / "2026-05-20 0700 running 1.fit").write_bytes(b"something else entirely")
    exporter.fold_old_layout(tmp_path)
    assert (tmp_path / "Activities" / "2026-05-20 0700 running 1.fit").read_bytes() == b"fit 2026-05-20 0700 running 1.fit"
    assert (tmp_path / "New 2026-11-14 0542" / "2026-05-20 0700 running 1.fit").exists()     # left, not lost
    assert not (tmp_path / "All sessions").exists()


def test_a_folder_of_the_users_own_called_new_is_left_alone(tmp_path):
    (tmp_path / "New ideas").mkdir()
    (tmp_path / "New ideas" / "plan.fit").write_bytes(b"x")
    exporter.fold_old_layout(tmp_path)
    assert (tmp_path / "New ideas" / "plan.fit").exists()


def test_a_stopped_v0_2_refresh_carries_on(tmp_path):
    old_layout(tmp_path, "All sessions", pending={"folder": "New 2026-11-15 0700"})
    assert exporter.summary(tmp_path)["unfinished"]
    exporter.refresh(FakeGarmin([]), tmp_path, [], 24, now=dt.datetime(2026, 11, 21, 7, 0))
    assert not exporter.summary(tmp_path)["unfinished"]
