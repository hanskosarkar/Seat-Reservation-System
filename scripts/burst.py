#!/usr/bin/env python3
"""On-sale burst against a running service. Standard library only.

    ./burst.sh https://your-service.example.com
    run: python3 scripts/burst.py http://localhost:8000 --requests 20000

Phases
  A  stampede   : many users, mixed single/multi-seat requests, a few hot
                  seats getting most of the demand, ~10% duplicate retries
                  that reuse the same idempotency key.
  B  hot seat   : N different users all reserve the SAME seat at once.
  C  limit      : one user fires 10 parallel reserves on a limit=4 show.
  D  idempotency: replay, key reuse with different seats, concurrent retries.
  E  cancel     : owner-only cancel, rebook, double-cancel never steals a seat.

It prints the outcome distribution (confirmed / replayed / declined by reason
/ 5xx / client errors) and a final reconciliation, and exits non-zero if any
correctness check fails.
"""
import argparse
import http.client
import json
import random
import sys
import threading
import time
import urllib.parse
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import ssl

# ----------------------------------------------------------------- http client
class Client:
    """Keep-alive HTTP client, one persistent connection per worker thread."""

    def __init__(self, base_url: str, timeout: float):
        u = urllib.parse.urlparse(base_url.rstrip("/"))
        self.scheme, self.host = u.scheme, u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.prefix = u.path
        self.timeout = timeout
        self.local = threading.local()

    def _connect(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        context = ssl._create_unverified_context()
        c = cls(self.host, self.port, timeout=self.timeout, context=context)
        self.local.conn = c
        return c

    def _reset(self):
        c = getattr(self.local, "conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        self.local.conn = None

    def request(self, method, path, body=None, token=None, headers=None):
        """Returns (status, json_body, lowercase_headers). status 0 = client error."""
        payload = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = f"Bearer {token}"
        err = None
        for _ in range(2):  # one transparent reconnect for a stale keep-alive socket
            c = getattr(self.local, "conn", None) or self._connect()
            try:
                c.request(method, self.prefix + path, body=payload, headers=h)
                r = c.getresponse()
                raw = r.read()
                try:
                    data = json.loads(raw) if raw else {}
                except ValueError:
                    data = {}
                return r.status, data, {k.lower(): v for k, v in r.getheaders()}
            except (http.client.HTTPException, OSError) as e:
                err = e
                self._reset()
        return 0, {"error": {"code": "CLIENT_ERROR", "message": str(err)}}, {}


# ----------------------------------------------------------------- helpers
class Report:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=""):
        self.checks.append((name, bool(ok)))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))
        return ok

    @property
    def ok(self):
        return all(ok for _, ok in self.checks)


def classify(status, body, headers):
    if status == 0:
        return "client_error"
    if status >= 500:
        return f"{status} server_error"
    if status == 201:
        return "idempotent_replay" if headers.get("idempotent-replay") == "true" else "confirmed"
    if status >= 400:
        return f"declined:{body.get('error', {}).get('code', status)}"
    return f"{status} ok"


def print_distribution(title, counter, total):
    print(f"\n{title}")
    for k, v in sorted(counter.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<36}{v:>8}  {100 * v / total:5.1f}%")


def is_bad(label):
    return label in ("client_error",) or label.endswith("server_error")


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))]


class Env:
    def __init__(self, client, workers):
        self.c = client
        self.workers = workers
        self.tokens = {}
        self.lock = threading.Lock()

    def token(self, user):
        with self.lock:
            t = self.tokens.get(user)
        if t:
            return t
        status, body, _ = self.c.request("POST", "/auth/token", {"user_id": user})
        if status != 200:
            raise SystemExit(f"cannot get a token for {user}: {status} {body}")
        with self.lock:
            self.tokens[user] = body["access_token"]
        return body["access_token"]

    def mint(self, users):
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(self.token, users))

    def new_show(self, seats, limit=4, price=25000):
        status, body, _ = self.c.request(
            "POST", "/shows",
            {"name": "burst", "seats": seats, "price_paise": price, "per_user_limit": limit},
            self.token("organiser"),
        )
        if status != 201:
            raise SystemExit(f"cannot create show: {status} {body}")
        return body["id"]

    def reserve(self, show, user, seats, key):
        return self.c.request("POST", f"/shows/{show}/reserve",
                              {"seats": seats, "idempotency_key": key}, self.token(user))

    def cancel(self, rid, user):
        return self.c.request("POST", f"/reservations/{rid}/cancel", None, self.token(user))

    def state(self, show):
        status, body, _ = self.c.request("GET", f"/shows/{show}")
        if status != 200:
            raise SystemExit(f"cannot read show: {status} {body}")
        return body


def reconcile(rep, st, label=""):
    total = st["available"] + st["held"] + st["confirmed"]
    rep.check(f"{label}available + held + confirmed == total_seats "
              f"({st['available']}+{st['held']}+{st['confirmed']}={total}, total={st['total_seats']})",
              total == st["total_seats"] == len(st["seats"]))


# ----------------------------------------------------------------- phase A
def phase_stampede(env, rep, a):
    print("\n" + "=" * 72)
    print(f"PHASE A  on-sale stampede: {a.requests} requests, {a.users} users, "
          f"{a.seats} seats, {a.hot_seats} hot seats, concurrency {a.concurrency}")
    print("=" * 72)
    rnd = random.Random(a.seed)
    hot = [f"H{i}" for i in range(1, a.hot_seats + 1)]
    cold = [f"S{i}" for i in range(1, a.seats - a.hot_seats + 1)]
    show = env.new_show(hot + cold, limit=a.limit)
    users = [f"fan{i}" for i in range(a.users)]
    env.mint(users)

    logical = max(1, int(a.requests / (1 + a.retry_ratio)))
    tasks = []
    for i in range(logical):
        user = rnd.choice(users)
        pick = lambda: rnd.choice(hot) if rnd.random() < a.hot_ratio else rnd.choice(cold)
        seats = [pick()]
        if rnd.random() < a.multi_ratio:  # multi-seat, random order, may overlap hot seats
            second = pick()
            if second != seats[0]:
                seats.append(second)
                rnd.shuffle(seats)
        tasks.append((user, seats, f"burst-{i}"))
    tasks += [rnd.choice(tasks) for _ in range(a.requests - len(tasks))]  # retries, same key
    rnd.shuffle(tasks)

    def run(t):
        user, seats, key = t
        t0 = time.perf_counter()
        res = env.reserve(show, user, seats, key)
        return t, res, time.perf_counter() - t0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.concurrency) as ex:
        results = list(ex.map(run, tasks))
    wall = time.perf_counter() - t0

    dist, lat = Counter(), []
    confirmed_seats, by_user, key_to_res = Counter(), Counter(), {}
    replay_mismatch = 0
    for (user, seats, key), (status, body, headers), dt in results:
        label = classify(status, body, headers)
        dist[label] += 1
        lat.append(dt)
        if label == "confirmed":
            for s in body["seats"]:
                confirmed_seats[s] += 1
            by_user[user] += len(body["seats"])
            key_to_res[(user, key)] = body["reservation_id"]
    for (user, seats, key), (status, body, headers), _ in results:
        if classify(status, body, headers) == "idempotent_replay":
            if key_to_res.get((user, key)) != body["reservation_id"]:
                replay_mismatch += 1

    print_distribution("Outcome distribution", dist, len(results))
    print(f"\n  wall {wall:.1f}s  |  {len(results) / wall:,.0f} req/s  |  latency p50 "
          f"{pct(lat, 50) * 1000:.0f}ms  p95 {pct(lat, 95) * 1000:.0f}ms  "
          f"p99 {pct(lat, 99) * 1000:.0f}ms  max {max(lat) * 1000:.0f}ms")

    st = env.state(show)
    print("\nChecks")
    rep.check("zero 5xx and zero client/connection errors", not any(is_bad(k) for k in dist),
              str({k: v for k, v in dist.items() if is_bad(k)}))
    rep.check("no seat confirmed to two users", all(v == 1 for v in confirmed_seats.values()),
              str([s for s, v in confirmed_seats.items() if v > 1][:5]))
    rep.check("every hot seat has at most one confirmation",
              all(confirmed_seats[h] <= 1 for h in hot))
    rep.check(f"no user above per-user limit ({a.limit})", all(v <= a.limit for v in by_user.values()),
              str([u for u, v in by_user.items() if v > a.limit][:5]))
    rep.check("every idempotent replay returned the ORIGINAL reservation", replay_mismatch == 0,
              f"{replay_mismatch} mismatches")
    rep.check(f"API confirmed ({st['confirmed']}) == seats in 'confirmed' responses "
              f"({sum(confirmed_seats.values())})", st["confirmed"] == sum(confirmed_seats.values()))
    api_status = {s["seat"]: s["status"] for s in st["seats"]}
    rep.check("every seat we were told is confirmed shows 'confirmed' in GET /shows/{id}",
              all(api_status.get(s) == "confirmed" for s in confirmed_seats))
    reconcile(rep, st)
    won = sum(1 for h in hot if confirmed_seats[h])
    print(f"\n  hot seats won: {won}/{len(hot)}   seats confirmed: {st['confirmed']}/{st['total_seats']}")


# ----------------------------------------------------------------- phase B
def phase_hot_seat(env, rep, a):
    print("\n" + "=" * 72)
    print(f"PHASE B  hot-seat storm: {a.storm_users} different users, same seat A12, at once")
    print("=" * 72)
    show = env.new_show([f"A{i}" for i in range(1, 101)])
    users = [f"storm{i}" for i in range(a.storm_users)]
    env.mint(users)
    with ThreadPoolExecutor(a.concurrency) as ex:
        res = list(ex.map(lambda u: env.reserve(show, u, ["A12"], f"storm-{u}"), users))
    dist = Counter(classify(*r) for r in res)
    print_distribution("Outcome distribution", dist, len(res))
    st = env.state(show)
    print("\nChecks")
    rep.check("exactly one 201", dist.get("confirmed", 0) == 1, str(dict(dist)))
    rep.check(f"all others declined SEAT_TAKEN ({a.storm_users - 1})",
              dist.get("declined:SEAT_TAKEN", 0) == a.storm_users - 1)
    rep.check("zero 5xx / client errors", not any(is_bad(k) for k in dist))
    rep.check("A12 is confirmed, only 1 seat confirmed",
              st["confirmed"] == 1 and any(s["seat"] == "A12" and s["status"] == "confirmed" for s in st["seats"]))
    reconcile(rep, st)


# ----------------------------------------------------------------- phase C
def phase_limit(env, rep, a):
    print("\n" + "=" * 72)
    print("PHASE C  per-user limit: one user, 10 parallel reserves, limit = 4")
    print("=" * 72)
    show = env.new_show([f"S{i}" for i in range(20)], limit=4)
    with ThreadPoolExecutor(10) as ex:
        res = list(ex.map(lambda i: env.reserve(show, "greedy", [f"S{i}"], f"g-{i}"), range(10)))
    dist = Counter(classify(*r) for r in res)
    print_distribution("Outcome distribution", dist, len(res))
    st = env.state(show)
    print("\nChecks")
    rep.check("exactly 4 confirmed", dist.get("confirmed", 0) == 4)
    rep.check("the other 6 declined PER_USER_LIMIT_EXCEEDED",
              dist.get("declined:PER_USER_LIMIT_EXCEEDED", 0) == 6)
    rep.check("at most 4 seats held by the user in the API", st["confirmed"] == 4)
    reconcile(rep, st)


# ----------------------------------------------------------------- phase D
def phase_idempotency(env, rep, a):
    print("\n" + "=" * 72)
    print("PHASE D  idempotency")
    print("=" * 72)
    show = env.new_show(["A1", "A2", "A3"])
    s1, b1, _ = env.reserve(show, "idem", ["A1"], "key-1")
    s2, b2, h2 = env.reserve(show, "idem", ["A1"], "key-1")
    s3, b3, _ = env.reserve(show, "idem", ["A2"], "key-1")
    print("\nChecks")
    rep.check("retry returns the original reservation (same id, replay header)",
              (s1, s2) == (201, 201) and b1 == b2 and h2.get("idempotent-replay") == "true")
    rep.check("same key, different seats -> 409 IDEMPOTENCY_KEY_REUSED",
              s3 == 409 and b3["error"]["code"] == "IDEMPOTENCY_KEY_REUSED")
    with ThreadPoolExecutor(40) as ex:
        res = list(ex.map(lambda _: env.reserve(show, "idem2", ["A3"], "storm-key"), range(40)))
    ids = {b.get("reservation_id") for s, b, _ in res if s == 201}
    rep.check("40 concurrent retries with one key -> all 201, one reservation",
              all(s == 201 for s, _, _ in res) and len(ids) == 1, str(Counter(s for s, _, _ in res)))
    st = env.state(show)
    rep.check("retries moved nothing extra (2 seats confirmed)", st["confirmed"] == 2)
    show2 = env.new_show(["A1"])
    s, b, _ = env.reserve(show2, "idem", ["A1"], "key-1")
    rep.check("same key on a different show books independently",
              s == 201 and b["show_id"] == show2)
    reconcile(rep, st)


# ----------------------------------------------------------------- phase E
def phase_cancel(env, rep, a):
    print("\n" + "=" * 72)
    print("PHASE E  cancel / release")
    print("=" * 72)
    show = env.new_show(["X1", "X2"])
    _, r, _ = env.reserve(show, "alice", ["X1"], "c-1")
    rid = r["reservation_id"]
    print("\nChecks")
    s, b, _ = env.cancel(rid, "bob")
    rep.check("non-owner cancel -> 403", s == 403 and b["error"]["code"] == "FORBIDDEN")
    s, b, _ = env.cancel(rid, "alice")
    rep.check("owner cancel -> 200 cancelled", s == 200 and b["status"] == "cancelled")
    s, rb, _ = env.reserve(show, "carol", ["X1"], "c-2")
    rep.check("released seat is re-bookable by another user", s == 201)
    s, b, _ = env.cancel(rid, "alice")
    st = env.state(show)
    rep.check("cancelling AGAIN does not touch carol's seat",
              s == 200 and any(x["seat"] == "X1" and x["status"] == "confirmed" for x in st["seats"]))
    rep.check("only carol's seat is confirmed", st["confirmed"] == 1)
    reconcile(rep, st)


# ----------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base_url", help="e.g. https://my-service.example.com or http://localhost:8000")
    p.add_argument("--requests", type=int, default=20000, help="phase A request count (default 20000)")
    p.add_argument("--concurrency", type=int, default=400, help="client threads (default 400)")
    p.add_argument("--users", type=int, default=2000)
    p.add_argument("--seats", type=int, default=1000, help="seats in the phase A show (max 5000)")
    p.add_argument("--hot-seats", type=int, default=10)
    p.add_argument("--hot-ratio", type=float, default=0.6, help="share of demand on hot seats")
    p.add_argument("--multi-ratio", type=float, default=0.1, help="share of 2-seat requests")
    p.add_argument("--retry-ratio", type=float, default=0.1, help="share of duplicate retries")
    p.add_argument("--limit", type=int, default=4, help="per-user limit for phase A")
    p.add_argument("--storm-users", type=int, default=500, help="phase B users on one seat")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--skip-stampede", action="store_true", help="skip phase A")
    a = p.parse_args()

    client = Client(a.base_url, a.timeout)
    status, body, _ = client.request("GET", "/readyz")
    print(f"target {a.base_url}   /readyz -> {status} {body}")
    if status != 200:
        raise SystemExit("service is not ready; aborting")

    env = Env(client, a.concurrency)
    rep = Report()
    if not a.skip_stampede:
        phase_stampede(env, rep, a)
    phase_hot_seat(env, rep, a)
    phase_limit(env, rep, a)
    phase_idempotency(env, rep, a)
    phase_cancel(env, rep, a)

    failed = [n for n, ok in rep.checks if not ok]
    print("\n" + "=" * 72)
    print(f"RESULT: {len(rep.checks) - len(failed)}/{len(rep.checks)} checks passed"
          + ("" if not failed else "  FAILED: " + "; ".join(failed)))
    print("=" * 72)
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
