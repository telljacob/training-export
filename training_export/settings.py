"""
The athlete's choices (a small JSON file in their own AppData) and their Personal API key (Windows' password store,
Credential Manager, never a file). Nothing here leaves the computer.
"""
import json
import os
import re
from pathlib import Path

from . import health

APP_NAME = "Training Export"
KEY_USER = "garmin-personal-api-key"
MAX_TIMES = 6              # automatic refreshes a day; a refresh after the first costs Garmin only a few calls
MIN_GAP_MIN = 60           # at least an hour between two of them
FIRST_MONTHS = (6, 12, 24, 36, 0)     # 0 = everything Garmin has

FOLDER_IDS = {      # Windows' own ids for these folders, so a Desktop moved to OneDrive is still found
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
}


def known_folder(name: str) -> Path:
    """The athlete's Downloads or Desktop folder, wherever Windows keeps it."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                        ("d4", ctypes.c_ubyte * 8)]
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(FOLDER_IDS[name], ctypes.byref(g))
        p = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p))
        path = p.value
        ctypes.windll.ole32.CoTaskMemFree(p)
        if path:
            return Path(path)
    except Exception:
        pass
    return Path.home() / ("Downloads" if name == "downloads" else "Desktop")


def suggested_folders() -> dict:
    return {k: str(known_folder(k) / APP_NAME) for k in FOLDER_IDS}


DEFAULTS = {
    "folder": str(known_folder("downloads") / APP_NAME),
    "first_months": 24,
    "sessions": True,             # the sessions' .fit files
    "kinds": list(health.ALL_KINDS),   # the health data kinds; none = no health file
    "mode": "manual",             # "manual" (the Refresh button) or "auto" (at the times below)
    "refresh_on_open": True,      # manual mode: refresh each time the window opens
    "times": ["07:00", "19:00"],  # auto mode
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME


def load() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads((config_dir() / "settings.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return clean(s)


def save(s: dict) -> dict:
    s = clean(s)
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "settings.json.part"
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    tmp.replace(d / "settings.json")
    return s


class Invalid(ValueError):
    """A choice that can't be saved, said in plain words."""


def clean_times(times) -> list:
    """'HH:MM' times, sorted, at most MAX_TIMES, at least MIN_GAP_MIN apart (also across midnight)."""
    out = []
    for t in times or []:
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(t))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise Invalid(f"{t!r} is not a time. Use hours and minutes, like 07:00.")
        out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    out = sorted(set(out))
    if len(out) > MAX_TIMES:
        raise Invalid(f"At most {MAX_TIMES} refreshes a day, so Garmin doesn't slow you down.")
    mins = [int(t[:2]) * 60 + int(t[3:]) for t in out]
    for a, b in zip(mins, mins[1:] + [mins[0] + 24 * 60] if len(mins) > 1 else []):
        if b - a < MIN_GAP_MIN:
            raise Invalid("Keep refreshes at least an hour apart.")
    return out


def clean(s: dict) -> dict:
    out = dict(DEFAULTS)
    out["folder"] = str(s.get("folder") or DEFAULTS["folder"])
    fm = s.get("first_months", DEFAULTS["first_months"])
    out["first_months"] = fm if fm in FIRST_MONTHS else DEFAULTS["first_months"]
    out["sessions"] = bool(s.get("sessions", True))
    out["kinds"] = [k for k in health.ALL_KINDS if k in (s.get("kinds") or [])]
    if not out["sessions"] and not out["kinds"]:
        raise Invalid("Choose something to download: sessions, health data or both.")
    out["mode"] = "auto" if s.get("mode") == "auto" else "manual"
    out["refresh_on_open"] = bool(s.get("refresh_on_open", True))
    out["times"] = clean_times(s.get("times", DEFAULTS["times"]))
    if out["mode"] == "auto" and not out["times"]:
        raise Invalid("Add at least one time, or choose the Refresh button instead.")
    return out


# -- the key ---------------------------------------------------------------------------------------------------------
def _keyring():
    import keyring                # Windows Credential Manager on Windows
    return keyring


def get_key():
    try:
        return _keyring().get_password(APP_NAME, KEY_USER)
    except Exception:
        return None


def set_key(key: str) -> None:
    _keyring().set_password(APP_NAME, KEY_USER, key.strip())


def delete_key() -> None:
    try:
        _keyring().delete_password(APP_NAME, KEY_USER)
    except Exception:
        pass
