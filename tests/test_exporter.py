import csv
import datetime as dt
import json
import re

import pytest

from training_export import exporter, garmin, health
from fakes import FakeGarmin, activity, health_replies

NOW = dt.datetime(2026, 11, 14, 7, 12)
# the pattern Brick by Brick's wellness reader takes a health file's date window from
WINDOW_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[-_](\d{4}-\d{2}-\d{2})")


def run(fake, root, now=NOW, kinds=health.ALL_KINDS, months=24, **kw):
    return exporter.refresh(fake, root, kinds, months, now=now, **kw)


def test_first_export_writes_fits_into_new_and_all(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-13 17:45:00", "cycling"), activity(2, "2026-11-14 07:12:00"),
                       activity(3, "2023-01-01 10:00:00")])              # older than 24 months: left out
    r = run(fake, tmp_path)
    new = tmp_path / "New 2026-11-14 0712"
    assert r["new_sessions"] == 2 and r["folder"] == str(new)
    assert sorted(p.name for p in new.glob("*.fit")) == ["2026-11-13 1745 cycling 1.fit", "2026-11-14 0712 running 2.fit"]
    assert len(list((tmp_path / "All sessions").glob("*.fit"))) == 2
    for p in new.glob("*.fit"):
        assert p.read_bytes()[8:12] == b".FIT"


def test_second_refresh_only_takes_whats_new(tmp_path):
    acts = [activity(1, "2026-11-13 17:45:00"), activity(2, "2026-11-14 07:12:00")]
    run(FakeGarmin(acts), tmp_path)
    acts.append(activity(3, "2026-11-20 06:30:00", "lap_swimming"))
    fake = FakeGarmin(acts)
    r = run(fake, tmp_path, now=dt.datetime(2026, 11, 21, 7, 0))
    assert fake.downloads == [3]
    assert [p.name for p in (tmp_path / "New 2026-11-21 0700").glob("*.fit")] == ["2026-11-20 0630 lap swimming 3.fit"]
    assert r["new_sessions"] == 1


def test_nothing_new_leaves_no_empty_folder(tmp_path):
    run(FakeGarmin([activity(1, "2026-11-13 17:45:00")]), tmp_path, kinds=[])
    r = run(FakeGarmin([activity(1, "2026-11-13 17:45:00")]), tmp_path, now=dt.datetime(2026, 11, 15, 8, 0), kinds=[])
    assert r["folder"] is None and not (tmp_path / "New 2026-11-15 0800").exists()


def test_health_csv_shape_name_and_overlap(tmp_path):
    day = dt.date(2026, 11, 13)
    fake = FakeGarmin([], health_replies(day))
    run(fake, tmp_path, now=dt.datetime(2026, 11, 14, 7, 12), months=1)
    files = list((tmp_path / "New 2026-11-14 0712").glob("health-*.csv"))
    assert [f.name for f in files] == ["health-2026-10-14-2026-11-14.csv"]
    m = WINDOW_RE.search(files[0].name)
    assert m.group(2) == "2026-11-14"
    rows = list(csv.reader(files[0].open(encoding="utf-8")))
    assert rows[0] == ["Timestamp", "Type", "Value"]
    got = {(r[1]): r[2] for r in rows[1:]}
    assert got["Sleep Hours"] == "7.5"
    assert got["Pulse"] == "48"
    assert got["HRV Night"] == "61"
    assert got["Sleep Respiration"] == "14.2" or got["Sleep Respiration"] == "14.3"
    assert got["Sleep Start"] == "22.5"
    assert got["Stress Level"] == "Max : 80 / Avg : 25"
    assert got["Body Battery"] == "Min : 20 / Max : 90 / Avg : 50"
    assert got["Weight Kilograms"] == "75.00"
    # the next refresh reaches 7 days back past the last export
    run(FakeGarmin([], health_replies(day)), tmp_path, now=dt.datetime(2026, 11, 21, 7, 0))
    assert [f.name for f in (tmp_path / "New 2026-11-21 0700").glob("health-*.csv")] == ["health-2026-11-07-2026-11-21.csv"]


def test_switched_off_kinds_are_neither_fetched_nor_written(tmp_path):
    day = dt.date(2026, 11, 13)
    fake = FakeGarmin([], health_replies(day))
    run(fake, tmp_path, months=1, kinds=["hrv"])
    assert all(p.startswith("/hrv-service") for p in fake.paths)
    rows = list(csv.reader(next((tmp_path).glob("New */health-*.csv")).open(encoding="utf-8")))
    assert {r[1] for r in rows[1:]} == {"HRV Night"}


def test_stop_keeps_what_was_downloaded_and_the_next_refresh_carries_on(tmp_path):
    acts = [activity(i, f"2026-11-{i:02d} 07:00:00") for i in range(1, 6)]
    calls = {"n": 0}
    def stop_after_two():
        calls["n"] += 1
        return calls["n"] > 2
    r = run(FakeGarmin(acts), tmp_path, should_stop=stop_after_two)
    assert r["stopped"] and r["new_sessions"] == 2
    state = json.loads((tmp_path / exporter.STATE_FILE).read_text())
    assert state["pending"] and state["last_export"] is None
    fake = FakeGarmin(acts)
    r2 = run(fake, tmp_path, now=dt.datetime(2026, 11, 14, 9, 0))
    assert fake.downloads == [3, 4, 5]
    assert r2["folder"].endswith("New 2026-11-14 0712")          # same folder as the stopped run
    assert len(list((tmp_path / "New 2026-11-14 0712").glob("*.fit"))) == 5


def test_rate_limit_stops_cleanly_and_resumes(tmp_path):
    acts = [activity(i, f"2026-11-{i:02d} 07:00:00") for i in range(1, 5)]
    with pytest.raises(garmin.RateLimited):
        run(FakeGarmin(acts, rate_limit_after=2), tmp_path)
    assert len(list((tmp_path / "All sessions").glob("*.fit"))) == 2
    assert not (tmp_path / exporter.LOCK_FILE).exists()
    fake = FakeGarmin(acts)
    run(fake, tmp_path)
    assert fake.downloads == [3, 4]


def test_a_failed_download_is_tried_again_next_time(tmp_path):
    acts = [activity(1, "2026-11-10 07:00:00"), activity(2, "2026-11-11 07:00:00")]
    r = run(FakeGarmin(acts, fail_ids={1}), tmp_path, kinds=[])
    assert r["failed"] == 1 and r["new_sessions"] == 1
    fake = FakeGarmin(acts)
    run(fake, tmp_path, now=dt.datetime(2026, 11, 14, 9, 0), kinds=[])
    assert fake.downloads == [1]


def test_manual_activities_have_no_file_and_are_skipped(tmp_path):
    fake = FakeGarmin([activity(1, "2026-11-10 07:00:00", manualActivity=True)])
    run(fake, tmp_path, kinds=[])
    assert fake.downloads == []


def test_a_second_refresh_at_the_same_time_is_refused(tmp_path):
    with exporter.Lock(tmp_path):
        with pytest.raises(exporter.Busy):
            run(FakeGarmin([]), tmp_path)


def test_log_and_state_hold_no_values(tmp_path):
    day = dt.date(2026, 11, 13)
    run(FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(day)), tmp_path, months=1)
    log = (tmp_path / exporter.LOG_FILE).read_text(encoding="utf-8")
    assert "1 new sessions" in log and "48" not in log and "61" not in log
    state = (tmp_path / exporter.STATE_FILE).read_text(encoding="utf-8")
    assert "48" not in state.replace("2026", "")


def test_everything_starts_health_at_the_first_session(tmp_path):
    fake = FakeGarmin([activity(1, "2026-10-20 07:00:00")], health_replies(dt.date(2026, 11, 13)))
    run(fake, tmp_path, months=0)
    assert [f.name for f in tmp_path.glob("New */health-*.csv")] == ["health-2026-10-20-2026-11-14.csv"]


def test_summary_for_the_window(tmp_path):
    run(FakeGarmin([activity(1, "2026-11-13 07:00:00")], health_replies(dt.date(2026, 11, 13))), tmp_path, months=1)
    s = exporter.summary(tmp_path)
    assert s["total_sessions"] == 1 and s["latest_new_name"] == "New 2026-11-14 0712"
    assert s["latest_new_health"] == 1 and s["first_session"] == "2026-11-13" and not s["unfinished"]


def test_months_ago():
    assert exporter.months_ago(dt.date(2026, 11, 30), 24) == dt.date(2024, 11, 28)
    assert exporter.months_ago(dt.date(2026, 1, 15), 1) == dt.date(2025, 12, 15)


def test_a_download_failing_three_refreshes_running_is_left_out(tmp_path):
    acts = [activity(1, "2026-10-01 07:00:00")]
    for day in (14, 15, 16):
        run(FakeGarmin(acts, fail_ids={1}), tmp_path, now=dt.datetime(2026, 11, day, 9, 0), kinds=[], months=3)
    fake = FakeGarmin(acts)
    run(fake, tmp_path, now=dt.datetime(2026, 11, 17, 9, 0), kinds=[], months=3)
    assert fake.downloads == []
