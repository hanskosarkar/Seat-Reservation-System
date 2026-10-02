"""Integration tests against a RUNNING service. Standard library + PyJWT only.

    BASE_URL=http://localhost:8000 python3 -m unittest discover -s tests -v

Each test creates its own fresh show, so tests are independent and the suite
can be re-run against the same database any number of times.
"""
import json
import os
import time
import unittest
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import jwt

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
SECRET = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")


def call(method, path, body=None, token=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}"), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), {k.lower(): v for k, v in e.headers.items()}
        except ValueError:
            return e.code, {}, {k.lower(): v for k, v in e.headers.items()}


def token(user):
    status, body, _ = call("POST", "/auth/token", {"user_id": user})
    assert status == 200, body
    return body["access_token"]


def new_show(seats, limit=4, price=25000, owner="organiser"):
    status, body, _ = call(
        "POST", "/shows",
        {"name": "t", "seats": seats, "price_paise": price, "per_user_limit": limit},
        token(owner),
    )
    assert status == 201, body
    return body["id"]


def reserve(show, user_token, seats, key=None, headers=None, extra=None):
    body = {"seats": seats, **(extra or {})}
    if key is not None:
        body["idempotency_key"] = key
    return call("POST", f"/shows/{show}/reserve", body, user_token, headers)


def state(show):
    status, body, _ = call("GET", f"/shows/{show}")
    assert status == 200, body
    return body


def seat_status(show, label):
    return next(s["status"] for s in state(show)["seats"] if s["seat"] == label)


def uniq():
    return uuid.uuid4().hex[:12]


class AuthTests(unittest.TestCase):
    def test_token_issue_and_validation(self):
        self.assertEqual(call("POST", "/auth/token", {"user_id": "a"})[0], 200)
        self.assertEqual(call("POST", "/auth/token", {"user_id": "  "})[0], 422)
        self.assertEqual(call("POST", "/auth/token", {})[0], 422)

    def test_bad_tokens_are_401(self):
        show = new_show(["A1"])
        for tok in (None, "garbage",
                    jwt.encode({"sub": "x", "exp": int(time.time()) - 5}, SECRET, "HS256"),
                    jwt.encode({"sub": "x", "exp": int(time.time()) + 99}, "wrong", "HS256")):
            s, b, _ = reserve(show, tok, ["A1"], key=uniq())
            self.assertEqual(s, 401, b)
            self.assertEqual(b["error"]["code"], "UNAUTHORIZED")

    def test_spoofed_user_in_body_is_ignored(self):
        show = new_show(["A1"])
        s, b, _ = reserve(show, token("alice"), ["A1"], key=uniq(),
                          extra={"user_id": "mallory"})
        self.assertEqual(s, 201, b)
        self.assertEqual(b["user_id"], "alice")


class ShowTests(unittest.TestCase):
    def test_create_requires_token_and_validates(self):
        body = {"name": "x", "seats": ["A1", "A2"], "price_paise": 100}
        self.assertEqual(call("POST", "/shows", body)[0], 401)
        t = token("o")
        bad = [
            {**body, "price_paise": 250.5}, {**body, "price_paise": "100"},
            {**body, "price_paise": -1}, {**body, "seats": ["A1", "A1"]},
            {**body, "seats": ["A1", " A1 "]}, {**body, "seats": []},
            {**body, "seats": ["A1", " "]}, {**body, "seats": ["X" * 21]},
            {**body, "name": "  "}, {**body, "per_user_limit": 0},
            {**body, "seats": [f"S{i}" for i in range(5001)]},
        ]
        for b in bad:
            self.assertEqual(call("POST", "/shows", b, t)[0], 422, b.get("price_paise"))
        raw = urllib.request.Request(
            BASE + "/shows", method="POST",
            data=b'{"name":"x","seats":["A1"],"price_paise":25000.0}',
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {t}"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(raw)
        self.assertEqual(cm.exception.code, 422)

    def test_create_and_read_state(self):
        sid = new_show(["A1", "A2", "A10", "B1"])
        st = state(sid)
        self.assertEqual((st["total_seats"], st["available"], st["held"], st["confirmed"]),
                         (4, 4, 0, 0))
        self.assertEqual([s["seat"] for s in st["seats"]], ["A1", "A2", "A10", "B1"])
        self.assertEqual(st["per_user_limit"], 4)

    def test_get_errors(self):
        self.assertEqual(call("GET", "/shows/not-a-uuid")[0], 422)
        s, b, _ = call("GET", f"/shows/{uuid.uuid4()}")
        self.assertEqual((s, b["error"]["code"]), (404, "NOT_FOUND"))


class ReserveTests(unittest.TestCase):
    def test_single_seat_reserve(self):
        sid = new_show(["A1", "A2"], price=25000)
        s, b, _ = reserve(sid, token("alice"), ["A1"], key=uniq())
        self.assertEqual(s, 201, b)
        self.assertEqual((b["status"], b["amount_paise"], b["user_id"], b["seats"], b["show_id"]),
                         ("confirmed", 25000, "alice", ["A1"], sid))
        st = state(sid)
        self.assertEqual((st["confirmed"], st["available"]), (1, 1))
        self.assertEqual(st["available"] + st["held"] + st["confirmed"], st["total_seats"])

    def test_taken_seat_is_409(self):
        sid = new_show(["A1"])
        reserve(sid, token("alice"), ["A1"], key=uniq())
        s, b, _ = reserve(sid, token("bob"), ["A1"], key=uniq())
        self.assertEqual((s, b["error"]["code"]), (409, "SEAT_TAKEN"))
        self.assertEqual(b["error"]["details"]["seats"], ["A1"])

    def test_not_found_and_validation(self):
        sid = new_show(["A1"])
        t = token("alice")
        s, b, _ = reserve(sid, t, ["ZZ"], key=uniq())
        self.assertEqual((s, b["error"]["code"]), (404, "SEAT_NOT_FOUND"))
        self.assertEqual(reserve(str(uuid.uuid4()), t, ["A1"], key=uniq())[0], 404)
        self.assertEqual(reserve("bad-id", t, ["A1"], key=uniq())[0], 422)
        self.assertEqual(reserve(sid, t, [], key=uniq())[0], 422)
        self.assertEqual(reserve(sid, t, ["A1", "A1"], key=uniq())[0], 422)
        self.assertEqual(reserve(sid, t, ["A1"])[0], 422)               # no key
        self.assertEqual(reserve(sid, t, ["A1"], key="  ")[0], 422)     # blank key
        self.assertEqual(reserve(sid, t, ["A1"], key="k" * 201)[0], 422)
        self.assertEqual(state(sid)["confirmed"], 0)

    def test_label_whitespace_is_trimmed(self):
        sid = new_show(["A1"])
        s, b, _ = reserve(sid, token("alice"), [" A1 "], key=uniq())
        self.assertEqual((s, b["seats"]), (201, ["A1"]))

    def test_key_via_header(self):
        sid = new_show(["A1", "A2"])
        t = token("alice")
        self.assertEqual(reserve(sid, t, ["A1"], headers={"Idempotency-Key": uniq()})[0], 201)
        k = uniq()
        self.assertEqual(reserve(sid, t, ["A2"], key=k, headers={"Idempotency-Key": k})[0], 201)
        self.assertEqual(reserve(sid, t, ["A2"], key="x", headers={"Idempotency-Key": "y"})[0], 422)


class MultiSeatTests(unittest.TestCase):
    def test_all_or_nothing(self):
        sid = new_show(["A1", "A2", "A3"])
        reserve(sid, token("bob"), ["A2"], key=uniq())
        s, b, _ = reserve(sid, token("alice"), ["A1", "A2"], key=uniq())
        self.assertEqual((s, b["error"]["code"]), (409, "SEAT_TAKEN"))
        self.assertEqual(seat_status(sid, "A1"), "available")  # nothing partially taken
        s, b, _ = reserve(sid, token("alice"), ["A3", "A1"], key=uniq())
        self.assertEqual(s, 201, b)
        self.assertEqual(b["amount_paise"], 50000)  # price x 2, integer paise
        self.assertEqual(state(sid)["confirmed"], 3)

    def test_crossed_multi_seat_requests_never_deadlock(self):
        errors = []
        for _ in range(25):
            sid = new_show(["A1", "A2", "A3"], limit=10)
            ta, tb = token("alice"), token("bob")
            with ThreadPoolExecutor(4) as ex:
                f = [ex.submit(reserve, sid, ta, ["A1", "A2", "A3"], uniq()),
                     ex.submit(reserve, sid, tb, ["A3", "A2", "A1"], uniq()),
                     ex.submit(reserve, sid, ta, ["A2", "A3"], uniq()),
                     ex.submit(reserve, sid, tb, ["A1", "A3"], uniq())]
                codes = [x.result()[0] for x in f]
            if any(c >= 500 for c in codes):
                errors.append(codes)
            st = state(sid)
            self.assertEqual(st["available"] + st["held"] + st["confirmed"], 3)
            self.assertLessEqual(st["confirmed"], 3)
        self.assertEqual(errors, [], "5xx / deadlock under crossed requests")


class LimitTests(unittest.TestCase):
    def test_parallel_requests_cannot_exceed_limit(self):
        sid = new_show([f"S{i}" for i in range(20)], limit=4)
        t = token("greedy")
        with ThreadPoolExecutor(10) as ex:
            res = list(ex.map(lambda i: reserve(sid, t, [f"S{i}"], key=uniq()), range(10)))
        dist = Counter((s, b.get("error", {}).get("code")) for s, b, _ in res)
        self.assertEqual(dist[(201, None)], 4, dist)
        self.assertEqual(dist[(409, "PER_USER_LIMIT_EXCEEDED")], 6, dist)
        self.assertEqual(state(sid)["confirmed"], 4)

    def test_multi_seat_request_over_limit(self):
        sid = new_show([f"S{i}" for i in range(10)], limit=4)
        s, b, _ = reserve(sid, token("u"), [f"S{i}" for i in range(5)], key=uniq())
        self.assertEqual((s, b["error"]["code"]), (409, "PER_USER_LIMIT_EXCEEDED"))
        self.assertEqual(state(sid)["confirmed"], 0)

    def test_limit_is_per_show_and_frees_on_cancel(self):
        sid = new_show([f"S{i}" for i in range(6)], limit=2)
        t = token("u")
        r1 = reserve(sid, t, ["S0", "S1"], key=uniq())[1]
        self.assertEqual(reserve(sid, t, ["S2"], key=uniq())[0], 409)
        self.assertEqual(call("POST", f"/reservations/{r1['reservation_id']}/cancel",
                              None, t)[0], 200)
        self.assertEqual(reserve(sid, t, ["S2"], key=uniq())[0], 201)


class IdempotencyTests(unittest.TestCase):
    def test_replay_returns_original(self):
        sid = new_show(["A1", "A2"])
        t = token("alice")
        k = uniq()
        s1, b1, h1 = reserve(sid, t, ["A1"], key=k)
        s2, b2, h2 = reserve(sid, t, ["A1"], key=k)
        self.assertEqual((s1, s2), (201, 201))
        self.assertEqual(b1, b2)
        self.assertNotIn("idempotent-replay", h1)
        self.assertEqual(h2.get("idempotent-replay"), "true")
        self.assertEqual(state(sid)["confirmed"], 1)

    def test_same_key_different_seats_is_409(self):
        sid = new_show(["A1", "A2"])
        t = token("alice")
        k = uniq()
        reserve(sid, t, ["A1"], key=k)
        s, b, _ = reserve(sid, t, ["A2"], key=k)
        self.assertEqual((s, b["error"]["code"]), (409, "IDEMPOTENCY_KEY_REUSED"))
        self.assertEqual(seat_status(sid, "A2"), "available")

    def test_seat_order_does_not_matter(self):
        sid = new_show(["A1", "A2"])
        t = token("alice")
        k = uniq()
        a = reserve(sid, t, ["A1", "A2"], key=k)[1]
        b = reserve(sid, t, ["A2", "A1"], key=k)
        self.assertEqual((b[0], b[1]["reservation_id"]), (201, a["reservation_id"]))

    def test_key_is_scoped_per_show_and_per_user(self):
        s1, s2 = new_show(["A1"]), new_show(["A1"])
        t = token("alice")
        k = uniq()
        r1, r2 = reserve(s1, t, ["A1"], key=k), reserve(s2, t, ["A1"], key=k)
        self.assertEqual((r1[0], r2[0]), (201, 201))
        self.assertNotEqual(r1[1]["reservation_id"], r2[1]["reservation_id"])
        self.assertEqual(r2[1]["show_id"], s2)
        self.assertEqual(state(s2)["confirmed"], 1)
        s3 = new_show(["A1"])
        k2 = uniq()
        self.assertEqual(reserve(s3, token("u1"), ["A1"], key=k2)[0], 201)
        self.assertEqual(reserve(s3, token("u2"), ["A1"], key=k2)[1]["error"]["code"], "SEAT_TAKEN")

    def test_concurrent_retries_book_exactly_once(self):
        sid = new_show(["A1", "A2"])
        t = token("alice")
        k = uniq()
        with ThreadPoolExecutor(40) as ex:
            res = list(ex.map(lambda _: reserve(sid, t, ["A1"], key=k), range(40)))
        self.assertEqual(Counter(s for s, _, _ in res), {201: 40})
        self.assertEqual(len({b["reservation_id"] for _, b, _ in res}), 1)
        self.assertEqual(state(sid)["confirmed"], 1)

    def test_replay_of_cancelled_reservation_does_not_rebook(self):
        sid = new_show(["A1"])
        t = token("alice")
        k = uniq()
        r = reserve(sid, t, ["A1"], key=k)[1]
        call("POST", f"/reservations/{r['reservation_id']}/cancel", None, t)
        s, b, _ = reserve(sid, t, ["A1"], key=k)
        self.assertEqual((s, b["status"]), (201, "cancelled"))
        self.assertEqual(seat_status(sid, "A1"), "available")


class CancelTests(unittest.TestCase):
    def test_cancel_releases_and_seat_is_rebookable(self):
        sid = new_show(["A1"])
        ta, tb = token("alice"), token("bob")
        r = reserve(sid, ta, ["A1"], key=uniq())[1]
        s, b, _ = call("POST", f"/reservations/{r['reservation_id']}/cancel", None, ta)
        self.assertEqual((s, b["status"]), (200, "cancelled"))
        self.assertEqual(seat_status(sid, "A1"), "available")
        self.assertEqual(reserve(sid, tb, ["A1"], key=uniq())[0], 201)

    def test_only_owner_can_cancel(self):
        sid = new_show(["A1"])
        r = reserve(sid, token("alice"), ["A1"], key=uniq())[1]
        s, b, _ = call("POST", f"/reservations/{r['reservation_id']}/cancel", None, token("bob"))
        self.assertEqual((s, b["error"]["code"]), (403, "FORBIDDEN"))
        self.assertEqual(seat_status(sid, "A1"), "confirmed")
        self.assertEqual(call("POST", f"/reservations/{r['reservation_id']}/cancel", None)[0], 401)

    def test_cancel_never_resurrects_a_rebooked_seat(self):
        sid = new_show(["A1"])
        ta, tb = token("alice"), token("bob")
        r = reserve(sid, ta, ["A1"], key=uniq())[1]
        path = f"/reservations/{r['reservation_id']}/cancel"
        call("POST", path, None, ta)
        rb = reserve(sid, tb, ["A1"], key=uniq())[1]
        s, b, _ = call("POST", path, None, ta)          # alice cancels AGAIN
        self.assertEqual((s, b["status"]), (200, "cancelled"))
        self.assertEqual(seat_status(sid, "A1"), "confirmed")   # bob keeps it
        self.assertEqual(state(sid)["confirmed"], 1)
        # and bob's own cancel still works
        self.assertEqual(call("POST", f"/reservations/{rb['reservation_id']}/cancel", None, tb)[0], 200)

    def test_cancel_errors(self):
        t = token("alice")
        s, b, _ = call("POST", f"/reservations/{uuid.uuid4()}/cancel", None, t)
        self.assertEqual((s, b["error"]["code"]), (404, "NOT_FOUND"))
        self.assertEqual(call("POST", "/reservations/bad/cancel", None, t)[0], 422)

    def test_multi_seat_cancel_releases_all(self):
        sid = new_show(["A1", "A2", "A3"])
        t = token("alice")
        r = reserve(sid, t, ["A1", "A2", "A3"], key=uniq())[1]
        call("POST", f"/reservations/{r['reservation_id']}/cancel", None, t)
        self.assertEqual(state(sid)["available"], 3)


class ConcurrencyTests(unittest.TestCase):
    def test_hot_seat_exactly_one_winner(self):
        sid = new_show(["A12", "A13"])
        tokens = [token(f"u{i}") for i in range(200)]
        with ThreadPoolExecutor(100) as ex:
            res = list(ex.map(lambda i: reserve(sid, tokens[i], ["A12"], key=uniq()), range(200)))
        dist = Counter((s, b.get("error", {}).get("code")) for s, b, _ in res)
        self.assertEqual(dist[(201, None)], 1, dist)
        self.assertEqual(dist[(409, "SEAT_TAKEN")], 199, dist)
        self.assertEqual(sum(v for (s, _), v in dist.items() if s >= 500), 0)
        st = state(sid)
        self.assertEqual((st["confirmed"], st["available"]), (1, 1))

    def test_cancel_and_rebook_race_keeps_invariant(self):
        ta = token("alice")
        tokens = [token(f"r{i}") for i in range(6)]
        for _ in range(15):
            sid = new_show(["A1"], limit=10)
            r = reserve(sid, ta, ["A1"], key=uniq())[1]
            with ThreadPoolExecutor(8) as ex:
                fc = ex.submit(call, "POST", f"/reservations/{r['reservation_id']}/cancel", None, ta)
                fr = [ex.submit(reserve, sid, tk, ["A1"], uniq()) for tk in tokens]
                codes = [fc.result()[0]] + [f.result()[0] for f in fr]
            self.assertFalse(any(c >= 500 for c in codes), codes)
            st = state(sid)
            self.assertEqual(st["available"] + st["held"] + st["confirmed"], 1)
            # exactly one winner can exist at the end: the seat is never double-held
            self.assertLessEqual(st["confirmed"], 1)


if __name__ == "__main__":
    unittest.main()
