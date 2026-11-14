"""Sessions and health data as two parts, each one off or on, and catching up when switched on later."""
import csv
import datetime as dt
import json

import pytest

from training_export import exporter, health, settings
from fakes import FakeGarmin, activity, health_replies

NOW = dt.datetime(2026, 11, 14, 7, 12)


def health_types(folder):
    f = next(folder.glob("health-*.csv"))
    return f.name, {r[1] for r in list(csv.reader(f.open(encoding="utf-8")))[1:]}


def test_sessions_off_downloads_no_files_and_lists_nothing(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(dt.date(2026, 11, 13)))
    calls = []
    fake.activities = lambda a, b: calls.append((a, b)) or []
    r = exporter.refresh(fake, tmp_path, health.ALL_KINDS, 1, sessions=False, now=NOW)
    assert r["new_sessions"] == 0 and calls == [] and fake.downloads == []
    assert not (tmp_path / exporter.ALL_DIR).exists()
    assert list((tmp_path / "New 2026-11-14 0712").glob("health-*.csv"))


def test_health_off_writes_no_csv_and_asks_garmin_nothing_about_health(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(dt.date(2026, 11, 13)))
    exporter.refresh(fake, tmp_path, [], 1, now=NOW)
    assert fake.paths == []
    assert not list(tmp_path.glob("New */health-*.csv"))


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
    name, types = health_types(tmp_path / "New 2026-11-21 0700")
    assert name == "health-2026-10-14-2026-11-21.csv"     # weight from the first start; hrv rides along
    assert types == {"HRV Night", "Weight Kilograms"}
    exporter.refresh(FakeGarmin([], health_replies(day)), tmp_path, ["hrv", "weight"], 1,
                     now=dt.datetime(2026, 11, 28, 7, 0))
    assert health_types(tmp_path / "New 2026-11-28 0700")[0] == "health-2026-11-14-2026-11-28.csv"


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
    assert (tmp_path / "All sessions" / "2026-05-20 0700 running 1.fit").exists() and not old.exists()
    assert health_types(tmp_path / "New 2026-11-21 0700")[0] == "health-2026-11-07-2026-11-21.csv"
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
