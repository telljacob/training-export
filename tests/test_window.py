"""The real page in a hidden window: Settings folds closed after a good save and says so beside its heading."""
import json
import sys
import time

import pytest

webview = pytest.importorskip("webview")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the window is WebView2, Windows only")

from training_export import app, exporter, settings  # noqa: E402


class SaveApi(app.Api):
    """The real Api, except saving answers from a list instead of writing settings or the scheduled task."""

    def __init__(self, replies):
        super().__init__()
        self._replies = list(replies)

    def save_settings(self, s):
        return self._replies.pop(0)


def test_settings_fold_after_a_good_save_and_stay_open_after_a_bad_one(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    export = tmp_path / "export"
    export.mkdir()
    (export / exporter.STATE_FILE).write_text(json.dumps({"done_ids": [], "last_export": "2026-11-14T07:12:00"}))
    settings.save(dict(settings.DEFAULTS, folder=str(export), refresh_on_open=False))
    monkeypatch.setattr(settings, "get_key", lambda: "TEST-KEY-not-real")

    api = SaveApi([{"ok": False, "message": "Choose a time for the automatic refresh."}, {"ok": True, "message": "Saved."}])
    w = webview.create_window("test", str(app.resource("index.html")), js_api=api, hidden=True, width=520, height=720)
    api._window = w
    seen = {}

    def js(expr):
        return w.evaluate_js(expr)

    def wait_for(expr, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if js(expr):
                return True
            time.sleep(0.1)
        return False

    def probe(_):
        try:
            seen["home"] = wait_for("!document.getElementById('home').hidden")
            js("document.getElementById('settingsBox').open = true")
            js("document.getElementById('saveSettings').click()")
            wait_for("document.getElementById('settingsMsg').textContent !== ''")
            seen["bad"] = js("[document.getElementById('settingsBox').open,"
                             " document.getElementById('settingsMsg').textContent,"
                             " document.getElementById('savedNote').textContent]")
            js("document.getElementById('saveSettings').click()")
            wait_for("!document.getElementById('settingsBox').open")
            time.sleep(0.3)
            seen["good"] = js("[document.getElementById('settingsBox').open,"
                              " document.getElementById('settingsMsg').textContent,"
                              " document.getElementById('savedNote').textContent]")
            js("document.getElementById('settingsBox').open = true")
            time.sleep(0.3)
            seen["reopened_note"] = js("document.getElementById('savedNote').textContent")
        finally:
            w.destroy()

    webview.start(probe, w)
    assert seen["home"]
    assert seen["bad"] == [True, "Choose a time for the automatic refresh.", ""]
    assert seen["good"] == [False, "", "Saved."]
    assert seen["reopened_note"] == ""
