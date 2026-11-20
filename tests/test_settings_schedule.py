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
    monkeypatch.setattr(settings, "MAC", False)
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


def test_mac_settings_live_in_application_support(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MAC", True)
    monkeypatch.setattr(settings.Path, "home", lambda: tmp_path)
    assert settings.config_dir() == tmp_path / "Library" / "Application Support" / "Training Export"


def test_mac_agent_has_one_time_per_refresh_and_runs_the_app_without_a_window():
    import plistlib
    p = plistlib.loads(schedule.agent_plist(["07:00", "19:30"],
                                            "/Applications/Training Export.app/Contents/MacOS/Training Export",
                                            "--refresh", "/Applications/Training Export.app/Contents/MacOS"))
    assert p["Label"] == schedule.AGENT_LABEL
    assert p["ProgramArguments"] == ["/Applications/Training Export.app/Contents/MacOS/Training Export", "--refresh"]
    assert p["StartCalendarInterval"] == [{"Hour": 7, "Minute": 0}, {"Hour": 19, "Minute": 30}]
    assert p["RunAtLoad"] is False


def test_mac_install_replaces_the_agent_and_remove_deletes_it(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(schedule, "MAC", True)
    monkeypatch.setattr(schedule.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(schedule.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(schedule, "_launchctl",
                        lambda *a: (calls.append(a), schedule.subprocess.CompletedProcess(a, 0, "", ""))[1])
    schedule.install(["06:00"])
    assert schedule.installed()
    assert calls[0] == ("bootout", f"gui/501/{schedule.AGENT_LABEL}")
    assert calls[1] == ("bootstrap", "gui/501", str(schedule.agent_path()))
    schedule.remove()
    assert not schedule.installed()


def test_mac_install_says_when_macos_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "MAC", True)
    monkeypatch.setattr(schedule.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(schedule.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(schedule, "_launchctl",
                        lambda *a: schedule.subprocess.CompletedProcess(a, 5, "", "Bootstrap failed: 5"))
    with pytest.raises(RuntimeError, match="macOS did not accept"):
        schedule.install(["06:00"])
