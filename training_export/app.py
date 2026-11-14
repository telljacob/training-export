"""
The window (pywebview) and the windowless refresh the scheduled task runs.

    Training Export.exe              the window
    Training Export.exe --refresh    one refresh, no window (what the automatic times run)
"""
import argparse
import datetime as dt
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import exporter, garmin, health, schedule, settings

KEY_PAGE = "https://connect.garmin.com/app/settings/personalApiToken"


def resource(name: str) -> Path:
    """The page's files: next to this module, also inside the packaged .exe (bundled as training_export/ui)."""
    return Path(__file__).resolve().parent / "ui" / name


class Api:
    """What the page calls (window.pywebview.api.*). Never returns the key."""

    def __init__(self):
        self._window = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._run = {"running": False, "label": "", "i": 0, "n": 0, "message": "", "error": False}

    # -- reading -----------------------------------------------------------------------------------------------------
    def state(self):
        s = settings.load()
        return {
            "has_key": bool(settings.get_key()),
            "settings": s,
            "summary": exporter.summary(Path(s["folder"])),
            "kinds": [{"id": k, "label": v[0]} for k, v in health.KINDS.items()],
            "first_months": list(settings.FIRST_MONTHS),
            "max_times": settings.MAX_TIMES,
            "run": dict(self._run),
        }

    def progress(self):
        return dict(self._run)

    # -- the key -----------------------------------------------------------------------------------------------------
    def save_key(self, key):
        key = (key or "").strip()
        if len(key) < 10:
            return {"ok": False, "message": "That doesn't look like a Personal API key. Copy the whole key from Garmin Connect."}
        try:
            c = garmin.Client(key)
            today = dt.date.today()
            n = len(c.activities(today - dt.timedelta(days=30), today))
        except garmin.GarminError as e:
            return {"ok": False, "message": str(e)}
        settings.set_key(key)
        return {"ok": True, "message": f"Key works. {n} activities in the last 30 days."}

    def remove_key(self):
        settings.delete_key()
        return {"ok": True}

    def open_key_page(self):
        webbrowser.open(KEY_PAGE)

    # -- settings ----------------------------------------------------------------------------------------------------
    def save_settings(self, s):
        try:
            s = settings.save(s)
        except settings.Invalid as e:
            return {"ok": False, "message": str(e)}
        try:
            if s["mode"] == "auto":
                schedule.install(s["times"])
            else:
                schedule.remove()
        except Exception as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": "Saved."}

    def choose_folder(self):
        import webview
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG) if self._window else None
        return picked[0] if picked else None

    def open_folder(self, which):
        s = settings.load()
        root = Path(s["folder"])
        target = {"new": exporter.summary(root)["latest_new"], "all": str(root / exporter.ALL_DIR),
                  "root": str(root)}.get(which)
        if target and Path(target).exists():
            os.startfile(target)

    # -- refreshing --------------------------------------------------------------------------------------------------
    def refresh(self):
        with self._lock:
            if self._run["running"]:
                return False
            self._stop.clear()
            self._run.update(running=True, label="Starting", i=0, n=0, message="", error=False)
        threading.Thread(target=self._refresh, daemon=True).start()
        return True

    def stop(self):
        self._stop.set()

    def _refresh(self):
        try:
            r = run_once(lambda label, i, n: self._run.update(label=label, i=i, n=n), self._stop.is_set)
            self._run.update(message=describe(r))
        except Exception as e:
            self._run.update(message=plain_error(e), error=True)
        finally:
            self._run["running"] = False


def run_once(progress=None, should_stop=None) -> dict:
    s = settings.load()
    client = garmin.Client(settings.get_key() or "")
    return exporter.refresh(client, Path(s["folder"]), s["kinds"], s["first_months"],
                            progress=progress, should_stop=should_stop)


def describe(r: dict) -> str:
    if r["stopped"]:
        return f"Stopped after {r['new_activities']} activities. Refresh again to carry on."
    parts = []
    if r["new_activities"]:
        parts.append(f"{r['new_activities']} new {'activity' if r['new_activities'] == 1 else 'activities'}")
    if r["health_days"]:
        parts.append(f"{r['health_days']} days of health data")
    msg = (", ".join(parts) + " ready.") if parts else "Nothing new since last time."
    if r["failed"]:
        msg += f" {r['failed']} could not be downloaded; the next refresh tries again."
    return msg


def plain_error(e: Exception) -> str:
    if isinstance(e, (garmin.GarminError, exporter.Busy)):
        return str(e)
    if isinstance(e, OSError):
        return "Couldn't write to the export folder. Check that it exists and isn't read-only."
    return f"Something went wrong ({type(e).__name__}). Try Refresh again."


def headless() -> int:
    """The scheduled refresh: no window. The outcome is in the folder's export log."""
    try:
        run_once()
        return 0
    except Exception:
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="Training Export")
    ap.add_argument("--refresh", action="store_true", help="one refresh without a window")
    args = ap.parse_args(argv)
    if args.refresh:
        return headless()
    import webview
    api = Api()
    api._window = webview.create_window("Training Export", str(resource("index.html")), js_api=api,
                                       width=520, height=720, min_size=(420, 560), background_color="#1f2228")
    webview.start()
    return 0
