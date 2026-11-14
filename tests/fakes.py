"""A fake Garmin: canned replies in the shapes the Personal API answers with. No network, no real key."""
import datetime as dt
import io
import zipfile

from training_export import garmin


def fit_bytes(tag: bytes = b"x") -> bytes:
    """Enough of a .fit for the marker check: 12-byte header with ".FIT" at byte 8."""
    return bytes([12, 16, 0, 0, 0, 0, 0, 0]) + b".FIT" + tag * 20


def zipped(data: bytes, name="123_ACTIVITY.fit") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


def activity(aid, when, kind="running", **extra):
    return {"activityId": aid, "startTimeLocal": when, "activityType": {"typeKey": kind}, **extra}


class FakeGarmin:
    """Stands in for garmin.Client in exporter tests: list, download, and the health GETs."""

    def __init__(self, acts=(), health=None, fail_ids=(), rate_limit_after=None):
        self.acts = list(acts)
        self.health = health or {}
        self.fail_ids = set(fail_ids)
        self.rate_limit_after = rate_limit_after
        self.downloads = []
        self.paths = []

    def activities(self, start, end):
        return [a for a in self.acts if start.isoformat() <= a["startTimeLocal"][:10] <= end.isoformat()]

    def download_fit(self, aid):
        if self.rate_limit_after is not None and len(self.downloads) >= self.rate_limit_after:
            raise garmin.RateLimited("slow down")
        if aid in self.fail_ids:
            raise garmin.GarminError("GET /download-service: HTTP 404")
        self.downloads.append(aid)
        return fit_bytes(str(aid).encode())

    def get(self, path, params=None, raw=False):
        self.paths.append(path)
        for prefix, reply in self.health.items():
            if path.startswith(prefix):
                return reply(path, params) if callable(reply) else reply
        return None


def health_replies(day: dt.date):
    d = day.isoformat()
    midnight_local_ms = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return {
        "/sleep-service/sleep/dailySleepSummariesByDate": [{
            "calendarDate": d, "sleepTimeSeconds": 7.5 * 3600, "deepSleepSeconds": 3600, "lightSleepSeconds": 4 * 3600,
            "remSleepSeconds": 1.5 * 3600, "awakeSleepSeconds": 1800, "averageRespirationValue": 14.25,
            "sleepStartTimestampLocal": midnight_local_ms - 90 * 60 * 1000}],          # 22:30 the evening before
        "/usersummary-service/v2/dailysummaries": {"data": [{
            "calendarDate": d, "heartRate": {"restingValue": 48}, "stress": {"avgLevel": 25, "maxLevel": 80},
            "bodyBattery": {"minValue": 20, "maxValue": 90}}]},
        "/wellness-service/wellness/bodyBattery/reports/daily": [{
            "date": d, "bodyBatteryValuesArray": [[0, 40], [1, 60]]}],
        "/hrv-service/hrv/daily/": {"hrvSummaries": [{"calendarDate": d, "lastNightAvg": 61}]},
        "/weight-service/weight/range/": {"dailyWeightSummaries": [{"allWeightMetrics": [
            {"weight": 75000, "date": midnight_local_ms + 7 * 3600 * 1000}]}]},
    }


class FakeConn:
    """http.client.HTTPSConnection stand-in for garmin.Client tests: answers from a list of (status, body)."""

    def __init__(self, script, log):
        self.script, self.log = script, log

    def request(self, method, path, headers=None):
        self.log.append((method, path, dict(headers or {})))

    def getresponse(self):
        status, body = self.script.pop(0)
        return _Resp(status, body)

    def close(self):
        pass


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def getheaders(self):
        return []
