"""
Health and recovery data as (Timestamp, Type, Value) rows: the shape and Type names of a TrainingPeaks MetricsExport,
which is what training apps (Brick by Brick's Upload among them) already read. The same rows, units and rules as the
original Garmin sync this was modelled on.

Each kind of data can be switched off. Only the Garmin calls an enabled kind needs are made.
"""
import datetime as dt

PIECE_DAYS = 28      # Garmin's range calls refuse long spans (the sleep one: 32 days answered, 41 did not)

# What the athlete can switch off, in the order the settings show them.
KINDS = {
    "sleep":        ("Sleep and its stages", ["Sleep Hours", "Time In Deep Sleep", "Time In Light Sleep",
                                              "Time In REM Sleep", "Time Awake"]),
    "breathing":    ("Breathing rate in sleep", ["Sleep Respiration"]),
    "bedtime":      ("Bedtime", ["Sleep Start"]),
    "resting_hr":   ("Resting heart rate", ["Pulse"]),
    "hrv":          ("Nightly HRV", ["HRV Night"]),
    "stress":       ("Stress", ["Stress Level"]),
    "body_battery": ("Body Battery", ["Body Battery"]),
    "weight":       ("Weight", ["Weight Kilograms"]),
}
ALL_KINDS = list(KINDS)


def num(x, nd=2) -> str:
    return f"{x:.{nd}f}".rstrip("0").rstrip(".") if isinstance(x, (int, float)) else ""


def sleep_start_h(ms) -> str:
    """Bedtime as hours that run on past midnight (22:30 -> 22.5, 01:30 -> 25.5), so an average of 23:30 and 00:30
    is midnight, not noon. Garmin's local timestamp is local wall time in ms. Noon is the cut: a nap is not a night."""
    if not isinstance(ms, (int, float)):
        return ""
    t = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    h = t.hour + t.minute / 60
    return num(h + 24 if h < 12 else h, 2)


def rows(client, start: dt.date, end: dt.date, kinds) -> list:
    """Rows for every day in [start, end], fetched in pieces of PIECE_DAYS."""
    kinds = set(kinds) & set(KINDS)
    out, a = [], start
    while kinds and a <= end:
        b = min(end, a + dt.timedelta(days=PIECE_DAYS - 1))
        out += _piece(client, a, b, kinds)
        a = b + dt.timedelta(days=1)
    return out


def _by(items, key) -> dict:
    return {x.get(key): x for x in (items or []) if isinstance(x, dict)}


def _piece(c, start: dt.date, end: dt.date, kinds: set) -> list:
    s0, s1 = start.isoformat(), end.isoformat()
    sleep = daily = bb = hrv = {}
    weights = []
    if kinds & {"sleep", "breathing", "bedtime"}:
        sleep = _by(c.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": s0, "endDate": s1}),
                    "calendarDate")
    if kinds & {"resting_hr", "stress", "body_battery"}:
        daily = _by((c.get("/usersummary-service/v2/dailysummaries", {"startDate": s0, "endDate": s1}) or {})
                    .get("data"), "calendarDate")
    if "body_battery" in kinds:
        # chunks=24: the day's readings in fine steps; the default 6 points gave an average 1-3 off Garmin's figure
        bb = _by(c.get("/wellness-service/wellness/bodyBattery/reports/daily",
                       {"startDate": s0, "endDate": s1, "chunks": 24}), "date")
    if "hrv" in kinds:
        hrv = _by((c.get(f"/hrv-service/hrv/daily/{s0}/{s1}") or {}).get("hrvSummaries"), "calendarDate")
    if "weight" in kinds:
        weights = (c.get(f"/weight-service/weight/range/{s0}/{s1}", {"includeAll": "true"}) or {}) \
            .get("dailyWeightSummaries") or []

    out = []
    day = start
    while day <= end:
        d = day.isoformat()
        ts = f"{d} 00:00:00"
        sl = sleep.get(d) or {}
        if sl.get("sleepTimeSeconds"):
            if "sleep" in kinds:
                for t, k in (("Sleep Hours", "sleepTimeSeconds"), ("Time In Deep Sleep", "deepSleepSeconds"),
                             ("Time In Light Sleep", "lightSleepSeconds"), ("Time In REM Sleep", "remSleepSeconds"),
                             ("Time Awake", "awakeSleepSeconds")):
                    if isinstance(sl.get(k), (int, float)):
                        out.append((ts, t, num(sl[k] / 3600, 6)))
            resp = sl.get("averageRespirationValue")
            if "breathing" in kinds and isinstance(resp, (int, float)) and resp > 0:
                out.append((ts, "Sleep Respiration", num(resp, 1)))
            if "bedtime" in kinds and sleep_start_h(sl.get("sleepStartTimestampLocal")):
                out.append((ts, "Sleep Start", sleep_start_h(sl["sleepStartTimestampLocal"])))
        ds = daily.get(d) or {}
        rhr = (ds.get("heartRate") or {}).get("restingValue")
        if "resting_hr" in kinds and isinstance(rhr, (int, float)) and rhr > 0:
            out.append((ts, "Pulse", str(int(rhr))))
        st = ds.get("stress") or {}
        if "stress" in kinds and st.get("avgLevel") is not None and st.get("maxLevel") is not None:
            out.append((ts, "Stress Level", f"Max : {int(st['maxLevel'])} / Avg : {int(st['avgLevel'])}"))
        night = (hrv.get(d) or {}).get("lastNightAvg")
        if "hrv" in kinds and isinstance(night, (int, float)) and night > 0:
            out.append((ts, "HRV Night", num(night, 1)))
        b = ds.get("bodyBattery") or {}
        levels = [v[1] for v in ((bb.get(d) or {}).get("bodyBatteryValuesArray") or [])
                  if isinstance(v, list) and len(v) > 1 and isinstance(v[1], (int, float))]
        if "body_battery" in kinds and b.get("minValue") is not None and b.get("maxValue") is not None and levels:
            out.append((ts, "Body Battery", f"Min : {int(b['minValue'])} / Max : {int(b['maxValue'])} / "
                                            f"Avg : {round(sum(levels) / len(levels))}"))
        day += dt.timedelta(days=1)

    for w in weights:
        for m in w.get("allWeightMetrics") or []:
            if m.get("weight") and m.get("date"):
                # 'date' is local wall time in ms (timestampGMT is the UTC one)
                local = dt.datetime.fromtimestamp(m["date"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                out.append((local, "Weight Kilograms", f"{m['weight'] / 1000:.2f}"))
    return out
