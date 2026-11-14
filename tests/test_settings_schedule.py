import pytest

from training_export import schedule, settings


def test_times_are_cleaned_and_sorted():
    assert settings.clean_times(["19:00", "7:00"]) == ["07:00", "19:00"]


@pytest.mark.parametrize("times", [["25:00"], ["7"], ["07:00", "07:30"], ["23:30", "00:10"],
                                   [f"{h:02d}:00" for h in range(0, 24, 3)]])
def test_bad_times_are_refused_in_plain_words(times):
    with pytest.raises(settings.Invalid):
        settings.clean_times(times)


def test_six_times_an_hour_apart_are_fine():
    assert len(settings.clean_times([f"{h:02d}:00" for h in (6, 9, 12, 15, 18, 21)])) == settings.MAX_TIMES


def test_auto_needs_a_time():
    with pytest.raises(settings.Invalid):
        settings.clean(dict(settings.DEFAULTS, mode="auto", times=[]))


def test_unknown_values_fall_back():
    s = settings.clean({"first_months": 7, "kinds": ["hrv", "nonsense"], "mode": "sometimes"})
    assert s["first_months"] == 24 and s["kinds"] == ["hrv"] and s["mode"] == "manual"


def test_settings_round_trip_in_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings.save(dict(settings.DEFAULTS, mode="auto", times=["06:00"]))
    assert settings.load()["times"] == ["06:00"]
    assert (tmp_path / "Training Export" / "settings.json").exists()
    assert "key" not in (tmp_path / "Training Export" / "settings.json").read_text().lower()


def test_task_has_one_trigger_per_time_and_runs_without_a_window():
    xml = schedule.task_xml(["07:00", "19:30"], r"C:\Program Files\Training Export\Training Export.exe",
                            "--refresh", r"C:\Program Files\Training Export")
    assert xml.count("<CalendarTrigger>") == 2
    assert "T07:00:00" in xml and "T19:30:00" in xml
    assert "<StartWhenAvailable>true" in xml and "InteractiveToken" in xml and "--refresh" in xml
