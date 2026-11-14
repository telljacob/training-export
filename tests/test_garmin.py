import base64
import json
import time

import pytest

from training_export import garmin
from fakes import FakeConn, fit_bytes, zipped

KEY = "TEST-KEY-not-real-0123456789"


def jwt(exp):
    part = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{part}.c2lnbmF0dXJlLXRlc3Q"


def client(script):
    log = []
    c = garmin.Client(KEY, connect=lambda: FakeConn(script, log), sleep=lambda s: None)
    return c, log


def test_key_goes_only_to_the_token_call_and_token_is_reused():
    tok = jwt(time.time() + 86400)
    c, log = client([(200, tok.encode()), (200, b"[]"), (200, b"[]")])
    c.get("/a")
    c.get("/b")
    assert [m for m, _, _ in log] == ["POST", "GET", "GET"]
    assert log[0][2]["Authorization"] == f"ApiKey {KEY}"
    assert all(KEY not in json.dumps(h) for _, _, h in log[1:])
    assert log[1][2]["Authorization"] == f"Bearer {tok}"


def test_rejected_key_says_so_plainly_without_the_key():
    c, _ = client([(401, f"bad key {KEY}".encode())])
    with pytest.raises(garmin.KeyRejected) as e:
        c.token()
    assert KEY not in str(e.value)


def test_errors_are_scrubbed_of_key_and_token():
    tok = jwt(time.time() + 86400)
    c, _ = client([(200, tok.encode()), (500, f"oops {tok} {KEY}".encode())])
    with pytest.raises(garmin.GarminError) as e:
        c.get("/x")
    assert KEY not in str(e.value) and tok not in str(e.value)
    assert "HTTP 500" in str(e.value)


def test_short_rate_limits_are_waited_out_long_ones_stop():
    tok = jwt(time.time() + 86400)
    waits = []
    log = []
    script = [(200, tok.encode()), (429, b'{"retryInSeconds": 5}'), (200, b"[1]")]
    c = garmin.Client(KEY, connect=lambda: FakeConn(script, log), sleep=waits.append)
    assert c.get("/x") == [1]
    assert waits == [6]
    c2, _ = client([(200, tok.encode()), (429, b'{"retryInSeconds": 900}')])
    with pytest.raises(garmin.RateLimited):
        c2.get("/x")


def test_activities_are_paged():
    tok = jwt(time.time() + 86400)
    page1 = json.dumps([{"activityId": i} for i in range(100)]).encode()
    page2 = json.dumps([{"activityId": 100}]).encode()
    c, log = client([(200, tok.encode()), (200, page1), (200, page2)])
    import datetime as dt
    assert len(c.activities(dt.date(2025, 1, 1), dt.date(2025, 2, 1))) == 101
    assert "start=100" in log[2][1]


def test_fit_comes_out_of_garmins_zip():
    f = fit_bytes()
    assert garmin.fit_from_download(zipped(f)) == f
    assert garmin.fit_from_download(f) == f
    assert garmin.is_fit(f) and not garmin.is_fit(b"PK\x03\x04 not a fit")


def test_repr_never_shows_secrets():
    c, _ = client([])
    assert KEY not in repr(c)


def test_empty_key_is_refused():
    with pytest.raises(garmin.KeyRejected):
        garmin.Client("  ")
