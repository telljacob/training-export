r"""
One refresh: new sessions as original .fit files, and health data as one CSV, into a folder of their own.

    <export folder>\
        New 2026-11-21 0712\        only what this refresh found: the athlete drags all of it onto Upload
            2026-11-21 0712 running 12345678901.fit
            health-2026-11-14-2026-11-21.csv
        All sessions\              every .fit so far, for a fresh start somewhere else
        export-state.json          what is already downloaded (ids and dates only)
        export log.txt             one line per refresh, counts only

Sessions and health data are two parts the athlete can switch on or off. The first export's start (how far back) is
asked once; after that each part picks up where it got to, and a part switched on later reaches back to that same
start. Health is tracked per kind, so a kind switched on later (weight, say) catches up the same way.

The health file's name carries its date window (start-end), which is how a training app can tell the newest export
of a day from an older one. Every health window reaches 7 days back past the last export, because Garmin re-scores a
night or two after it ends.

A refresh can be stopped, or stopped by Garmin's rate limit, at any point: every downloaded file is recorded at once,
and the next refresh carries on in the same "New" folder.
"""
import csv
import datetime as dt
import io
import json
import os
import re
import time
from pathlib import Path

from . import garmin, health

STATE_FILE = "export-state.json"
LOG_FILE = "export log.txt"
LOCK_FILE = ".export.lock"
ALL_DIR = "All sessions"
OLD_ALL_DIR = "All activities"     # v0.1.0's name, renamed on the first refresh
HEALTH_OVERLAP_DAYS = 7
ACTIVITY_OVERLAP_DAYS = 3     # a watch that syncs a day late still has its session picked up
LOCK_STALE_S = 12 * 3600
MAX_TRIES = 3                 # a download that fails this many refreshes running is left out (said in the log)
EARLIEST = dt.date(2005, 1, 1)


class Busy(RuntimeError):
    """Another refresh (the window, or a scheduled one) is running on this folder."""


# -- small helpers ---------------------------------------------------------------------------------------------------
def months_ago(today: dt.date, months: int) -> dt.date:
    """The same day `months` back (28th at most, so every month has it). 0 means everything Garmin has."""
    if months <= 0:
        return EARLIEST
    y, m = divmod(today.month - 1 - months, 12)
    return dt.date(today.year + y, m + 1, min(today.day, 28))


def file_name(a: dict) -> str:
    """'2026-11-21 0712 running 12345678901.fit': sorts by date, says the sport, never collides."""
    start = (a.get("startTimeLocal") or "")[:16]
    stamp = start[:10] + " " + start[11:16].replace(":", "") if len(start) >= 16 else "unknown-date"
    kind = ((a.get("activityType") or {}).get("typeKey") or "activity").replace("_", " ")
    kind = re.sub(r"[^a-z0-9 ]", "", kind.lower()).strip() or "activity"
    return f"{stamp} {kind} {int(a['activityId'])}.fit"


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def health_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Timestamp", "Type", "Value"])
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _first_session_date(folder: Path):
    names = sorted(p.name for p in folder.glob("*.fit")) if folder.exists() else []
    return names[0][:10] if names and re.match(r"\d{4}-\d{2}-\d{2}", names[0]) else None


def _all_dir(root: Path) -> Path:
    old, new = root / OLD_ALL_DIR, root / ALL_DIR
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


# -- state -----------------------------------------------------------------------------------------------------------
def load_state(root: Path) -> dict:
    try:
        s = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    s.setdefault("done_ids", [])
    s.setdefault("failures", {})          # session id -> {"date": day, "tries": n}: tried again next refresh
    for k in ("last_export", "first_from", "activities_through", "pending"):
        s.setdefault(k, None)
    ht = s.get("health_through")
    if isinstance(ht, str):               # v0.1.0 kept one date for every kind (all were on)
        ht = {k: ht for k in health.ALL_KINDS}
    s["health_through"] = ht if isinstance(ht, dict) else {}
    if not s["first_from"] and s["activities_through"]:     # v0.1.0 did not record where the export started
        s["first_from"] = _first_session_date(root / ALL_DIR) or _first_session_date(root / OLD_ALL_DIR) \
            or s["activities_through"]
    if isinstance(s["pending"], dict) and "folder" not in s["pending"]:
        s["pending"] = None
    return s


def save_state(root: Path, state: dict) -> None:
    write_atomic(root / STATE_FILE, json.dumps(state, indent=1).encode("utf-8"))


def summary(root: Path) -> dict:
    """What the window shows: latest export, counts, the newest 'New' folder."""
    s = load_state(root)
    news = sorted((p for p in root.glob("New *") if p.is_dir()), key=lambda p: p.name) if root.exists() else []
    latest = news[-1] if news else None
    all_dir = root / ALL_DIR if (root / ALL_DIR).exists() else root / OLD_ALL_DIR

    def count(folder, pattern):
        return sum(1 for _ in folder.glob(pattern)) if folder and folder.exists() else 0
    return {
        "folder": str(root),
        "all_dir": str(all_dir),
        "last_export": s["last_export"],
        "first_from": s["first_from"],
        "health_through": max(s["health_through"].values(), default=None),
        "first_session": _first_session_date(all_dir),
        "total_sessions": count(all_dir, "*.fit"),
        "latest_new": str(latest) if latest else None,
        "latest_new_name": latest.name if latest else None,
        "latest_new_sessions": count(latest, "*.fit"),
        "latest_new_health": count(latest, "health-*.csv"),
        "unfinished": bool(s["pending"]),
    }


# -- lock ------------------------------------------------------------------------------------------------------------
class Lock:
    def __init__(self, root: Path):
        self.path = root / LOCK_FILE
        self.fd = None

    def __enter__(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                self.path.unlink()
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise Busy("A refresh is already running for this folder.") from None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                self.path.unlink()
            except OSError:
                pass


# -- the refresh -----------------------------------------------------------------------------------------------------
def _from(through, first: dt.date, overlap: int) -> dt.date:
    """Where a part picks up: a few days before what it already has, or the first export's start."""
    return dt.date.fromisoformat(through) - dt.timedelta(days=overlap) if through else first


def refresh(client, root: Path, kinds, first_months: int, sessions: bool = True, now: dt.datetime = None,
            progress=None, should_stop=None) -> dict:
    """Run one refresh: session files (when `sessions`) and the health kinds in `kinds` (none: no health file).
    Returns counts for the window; raises garmin.GarminError (KeyRejected, RateLimited) or Busy. Whatever was
    downloaded before an error or a stop is kept, and the next refresh carries on.

    `first_months` only matters the first time: it sets where the export starts."""
    now = now or dt.datetime.now()
    today = now.date()
    progress = progress or (lambda *a: None)
    should_stop = should_stop or (lambda: False)
    kinds = [k for k in health.ALL_KINDS if k in (kinds or [])]
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    with Lock(root):
        state = load_state(root)
        all_dir = _all_dir(root)
        if not state["first_from"]:
            state["first_from"] = months_ago(today, first_months).isoformat()
        first = dt.date.fromisoformat(state["first_from"])
        if state["pending"]:
            batch = root / state["pending"]["folder"]
        else:
            batch = root / f"New {now:%Y-%m-%d %H%M}"
            state["pending"] = {"folder": batch.name}
            save_state(root, state)
        batch.mkdir(exist_ok=True)

        act_from = _from(state["activities_through"], first, ACTIVITY_OVERLAP_DAYS)
        failures = state["failures"]
        retry = [f["date"] for f in failures.values() if f["tries"] < MAX_TRIES]
        if retry:                                   # reach back far enough to try earlier failed downloads again
            act_from = min(act_from, dt.date.fromisoformat(min(retry)))
        health_from = min((_from(state["health_through"].get(k), first, HEALTH_OVERLAP_DAYS) for k in kinds),
                          default=today)

        result = {"folder": str(batch), "new_sessions": 0, "failed": 0, "health_rows": 0, "health_days": 0,
                  "stopped": False}
        error = None
        try:
            acts = []
            everything = kinds and health_from <= EARLIEST
            if sessions or everything:
                progress("Looking for sessions", 0, 0)
                listed = client.activities(min(act_from, health_from) if everything else act_from, today)
                if everything:          # "everything Garmin has": health from the first session, not from 2005
                    dates = [a["startTimeLocal"][:10] for a in listed if a.get("startTimeLocal")]
                    health_from = dt.date.fromisoformat(min(dates)) if dates else months_ago(today, 24)
                if sessions:
                    done = set(state["done_ids"])
                    gave_up = {k for k, f in failures.items() if f["tries"] >= MAX_TRIES}
                    acts = [a for a in listed
                            if a.get("activityId") and int(a["activityId"]) not in done
                            and not a.get("manualActivity") and str(int(a["activityId"])) not in gave_up
                            and (a.get("startTimeLocal") or "")[:10] >= act_from.isoformat()]
                    acts.sort(key=lambda a: a.get("startTimeLocal") or "")

            if acts:
                all_dir.mkdir(exist_ok=True)
            for i, a in enumerate(acts, 1):
                if should_stop():
                    result["stopped"] = True
                    break
                progress("Session", i, len(acts))
                aid = str(int(a["activityId"]))
                try:
                    data = client.download_fit(a["activityId"])
                    if not garmin.is_fit(data):
                        raise garmin.GarminError("not a .fit file")
                except (garmin.KeyRejected, garmin.RateLimited):
                    raise
                except garmin.GarminError:
                    result["failed"] += 1      # tried again next refresh: it is not recorded as done
                    f = failures.setdefault(aid, {"date": (a.get("startTimeLocal") or today.isoformat())[:10],
                                                  "tries": 0})
                    f["tries"] += 1
                    save_state(root, state)
                    continue
                name = file_name(a)
                write_atomic(batch / name, data)
                write_atomic(all_dir / name, data)
                state["done_ids"].append(int(a["activityId"]))
                failures.pop(aid, None)
                save_state(root, state)
                result["new_sessions"] += 1

            if not result["stopped"] and kinds:
                progress("Health data", 0, 0)
                rows = health.rows(client, health_from, today, kinds)
                for old in batch.glob("health-*.csv"):
                    old.unlink()
                if rows:
                    write_atomic(batch / f"health-{health_from.isoformat()}-{today.isoformat()}.csv", health_csv(rows))
                result["health_rows"] = len(rows)
                result["health_days"] = len({r[0][:10] for r in rows})

            if not result["stopped"]:
                state.update(pending=None, last_export=now.isoformat(timespec="seconds"))
                if sessions:
                    state["activities_through"] = today.isoformat()
                for k in kinds:
                    state["health_through"][k] = today.isoformat()
                save_state(root, state)
                if not any(batch.iterdir()):
                    batch.rmdir()
                    result["folder"] = None
        except Exception as e:
            error = e
            save_state(root, state)
            raise
        finally:
            _log(root, now, result, error)
        return result


def _log(root: Path, now: dt.datetime, r: dict, error) -> None:
    """Counts only: never a value, a key or a token."""
    if error is not None:
        what = f"stopped: {type(error).__name__}"
    elif r["stopped"]:
        what = "stopped by you"
    else:
        what = "done"
    line = (f"{now:%Y-%m-%d %H:%M} {what}; {r['new_sessions']} new sessions, {r['failed']} failed, "
            f"{r['health_days']} days of health data\n")
    try:
        with open(root / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
