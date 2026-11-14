"""
Garmin's official Personal API, read-only, with the athlete's own Personal API key.

The key is passed in (it lives in Windows' password store, see settings.py). It goes only to Garmin's token call;
the access token it buys (a JWT, valid up to 24 h) stays in memory for one run. Neither ever appears in output, logs
or exceptions: errors carry the HTTP status, the path and at most a short, scrubbed piece of Garmin's reply.
Requests go only to https://personal.connectapi.garmin.com.

Endpoints and auth follow Garmin's own client (github.com/garmin/connect-personal-api-python).
"""
import base64
import http.client
import io
import json
import re
import time
import zipfile
from datetime import date
from urllib.parse import urlencode

HOST = "personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
TIMEOUT = 60
PAGE = 100
RETRY_MAX_S = 120          # the longest 429 wait waited out; longer stops the run (the next refresh carries on)
TRIES = 6


class GarminError(RuntimeError):
    """A failed call, described without any secret."""


class KeyRejected(GarminError):
    """Garmin refused the key: wrong, expired or deleted on Garmin Connect."""


class RateLimited(GarminError):
    """Garmin asked to slow down for longer than this run waits."""


def scrub(text: str, *secrets: str) -> str:
    """At most 200 characters of a reply, with any secret and anything token-shaped removed."""
    for s in secrets:
        if s:
            text = text.replace(s, "<hidden>")
    text = re.sub(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "<token>", text)
    return text[:200]


def jwt_exp(tok: str) -> float:
    """The token's expiry from its own claims; one hour if it can't be read (only used to decide when to renew)."""
    try:
        payload = tok.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except Exception:
        return time.time() + 3600


class Client:
    """One run's connection: swaps the key for a token once, then GETs with it."""

    def __init__(self, key: str, connect=None, sleep=None):
        if not key or not key.strip():
            raise KeyRejected("no Personal API key saved")
        self._key = key.strip()
        self._token = None
        self._expires = 0.0
        self._connect = connect or (lambda: http.client.HTTPSConnection(HOST, timeout=TIMEOUT))
        self._sleep = sleep or time.sleep
        self.requests = 0

    def __repr__(self):                       # never the key or the token
        return f"Client(host={HOST!r}, token={'set' if self._token else 'none'})"

    # -- transport ---------------------------------------------------------------------------------------------------
    def _once(self, method: str, path: str, headers: dict) -> tuple:
        conn = self._connect()
        try:
            conn.request(method, path, headers=headers)
            r = conn.getresponse()
            self.requests += 1
            return r.status, r.read(), dict(r.getheaders())
        except (OSError, http.client.HTTPException) as e:
            raise GarminError(f"{method} {path.split('?')[0]}: no answer from Garmin ({type(e).__name__})") from None
        finally:
            conn.close()

    def _call(self, method: str, path: str, headers: dict) -> tuple:
        """Garmin answers 429 with {"retryInSeconds": n} when asked too fast. Waits of up to two minutes are waited
        out, a few times; anything longer raises RateLimited so the run can stop cleanly and carry on next time."""
        for attempt in range(TRIES):
            status, body, hdrs = self._once(method, path, headers)
            if status != 429:
                return status, body, hdrs
            try:
                wait = float(json.loads(body)["retryInSeconds"])
            except (ValueError, KeyError, TypeError):
                wait = 30.0
            if not 0 <= wait <= RETRY_MAX_S or attempt == TRIES - 1:
                break
            self._sleep(wait + 1)
        raise RateLimited("Garmin asked to slow down. The next refresh carries on where this one stopped.")

    def token(self) -> str:
        if self._token and self._expires - time.time() > 300:
            return self._token
        status, body, _ = self._call("POST", TOKEN_PATH, {"Accept": "*/*", "Authorization": f"ApiKey {self._key}"})
        if status in (400, 401, 403):
            raise KeyRejected("Garmin did not accept the Personal API key. It may have expired or been deleted on "
                              "Garmin Connect: make a new one and paste it in.")
        if status != 200:
            raise GarminError(f"token: HTTP {status} {scrub(body.decode('utf-8', 'replace'), self._key)}")
        tok = body.decode("utf-8").strip()
        self._token, self._expires = tok, jwt_exp(tok)
        return tok

    def get(self, path: str, params: dict = None, raw: bool = False):
        full = path + ("?" + urlencode(params) if params else "")
        tok = self.token()
        status, body, _ = self._call("GET", full, {
            "Accept": "*/*" if raw else "application/json", "Authorization": f"Bearer {tok}"})
        if status == 204:                     # no content: a day with no record (HRV on a night not worn)
            return None
        if status == 401:
            self._token = None
            raise KeyRejected("Garmin stopped accepting the key during the run. Paste a new key and refresh again.")
        if status != 200:
            raise GarminError(f"GET {path}: HTTP {status} {scrub(body.decode('utf-8', 'replace'), self._key, tok)}")
        return body if raw else json.loads(body or b"null")

    # -- the calls this uses -----------------------------------------------------------------------------------------
    def activities(self, start: date, end: date) -> list:
        """Every activity between two dates (both included), paged."""
        out, offset = [], 0
        while True:
            page = self.get("/activitylist-service/activities/search/activities", {
                "start": offset, "limit": PAGE, "startDate": start.isoformat(), "endDate": end.isoformat()}) or []
            out += page
            if len(page) < PAGE:
                return out
            offset += PAGE

    def download_fit(self, activity_id) -> bytes:
        """The activity's original .fit (Garmin serves it inside a zip)."""
        return fit_from_download(self.get(f"/download-service/files/activity/{int(activity_id)}", raw=True))


def fit_from_download(data: bytes) -> bytes:
    """The .fit inside Garmin's zip (or the bytes themselves when it already is one)."""
    if data[:2] != b"PK":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".fit")]
        if not names:
            raise GarminError("the activity download held no .fit file")
        return z.read(names[0])


def is_fit(data: bytes) -> bool:
    """The marker Brick by Brick's Upload checks: ".FIT" at byte 8."""
    return len(data) >= 12 and data[8:12] == b".FIT"
