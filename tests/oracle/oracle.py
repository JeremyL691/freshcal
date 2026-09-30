"""Independent brute-force reference for release generation and verdicts (ported by T-7.1).

Ported from an external reviewer's independent implementation with its semantics unchanged; this file is the reference, so nothing in
`freshcal.core.schedule` / `freshcal.core.verdict` may be used to justify its results.

Deliberately naive and independent of freshcal.core.schedule/verdict and of croniter:
- cron matching: own 5-field matcher (day_or semantics, 'L' day-of-month) evaluated per local date;
- business-day predicate: own implementation of §3.3 reading the provider's holiday dicts;
- local -> UTC: brute force over the UTC offsets in effect near the wall time (only UTC->local
  conversions are used); a wall time with two preimages resolves to the earliest (fold=0 first
  occurrence), one with none (gap) resolves to the latest candidate (shifted forward by the gap);
- releases: enumerate every nominal of a generously padded date range in generation order,
  resolve, floor, dedup "first nominal wins", filter, sort;
- verdict: literal §3.7.2 with complete enumeration (no chunking, no streaming, no fast path).

This suite is the proof obligation for every change to `src/freshcal/core/`: such a change is
"no semantic change" only together with an oracle run that shows 0 disagreements. The only FreshCal imports allowed here are
`freshcal.core.model`, `freshcal.core.errors` and a calendar provider passed in as an argument.
"""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

H = timedelta(days=1830)
MAX_ROLL = 31
CAP = 10_000


class OracleError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- cron matcher
def _field(spec, lo, hi):
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
            if step != 1:
                b = hi
        out.update(range(a, b + 1, step))
    return out


@dataclass(frozen=True)
class Cron:
    minutes: frozenset
    hours: frozenset
    dom: frozenset | None  # None = '*'
    dom_last: bool
    months: frozenset
    dow: frozenset | None  # None = '*', 0=Sunday
    nth: tuple[tuple[int, int], ...] = ()  # (weekday 0=Sunday, ordinal 1-5) from `N#O`

    @staticmethod
    def parse(expr):
        m, h, dom, mon, dow = expr.split()
        dom_last = False
        if dom == "*":
            dset = None
        else:
            parts = dom.split(",")
            dom_last = "L" in parts
            rest = [p for p in parts if p != "L"]
            dset = frozenset(_field(",".join(rest), 1, 31)) if rest else frozenset()
        literals = []
        nth = []
        for part in dow.split(",") if dow != "*" else []:
            if "#" in part:
                day_text, ordinal_text = part.split("#")
                nth.append((int(day_text) % 7, int(ordinal_text)))
            else:
                literals.append(part)
        if literals and nth:
            # Standard cron does not define mixing plain days with nth weekdays, croniter
            # raises CroniterUnsupportedSyntaxError and FreshCal's loader turns that into
            # E202; the oracle refuses it too rather than inventing a reading (T-8.2's
            # documented grammar exclusion).
            raise ValueError(f"day-of-week mixes literals and nth weekdays: {dow!r}")
        dws = frozenset(d % 7 for d in _field(",".join(literals), 0, 7)) if literals else None
        return Cron(
            frozenset(_field(m, 0, 59)),
            frozenset(_field(h, 0, 23)),
            dset,
            dom_last,
            frozenset(_field(mon, 1, 12)),
            dws,
            tuple(nth),
        )

    @staticmethod
    def nth_weekday_day(d: date, weekday: int, ordinal: int) -> int | None:
        """Day-of-month of the ``ordinal``-th ``weekday`` in ``d``'s month, or None if absent.

        Independent of croniter: count forward from the month's first day. An ordinal of 5
        is simply absent in a month with only four such weekdays.
        """
        first = date(d.year, d.month, 1)
        first_cron_dow = (first.weekday() + 1) % 7
        day = 1 + (weekday - first_cron_dow) % 7 + 7 * (ordinal - 1)
        return day if day <= _cal.monthrange(d.year, d.month)[1] else None

    def day_matches(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        last = _cal.monthrange(d.year, d.month)[1]
        dom_ok = (
            None
            if self.dom is None and not self.dom_last
            else ((self.dom is not None and d.day in self.dom) or (self.dom_last and d.day == last))
        )
        cron_dow = (d.weekday() + 1) % 7
        if self.dow is None and not self.nth:
            dow_ok = None  # the field is `*`: it constrains nothing
        else:
            dow_ok = (self.dow is not None and cron_dow in self.dow) or any(
                weekday == cron_dow and d.day == self.nth_weekday_day(d, weekday, ordinal)
                for weekday, ordinal in self.nth
            )
        if dom_ok is None and dow_ok is None:
            return True
        if dom_ok is None:
            return dow_ok
        if dow_ok is None:
            return dom_ok
        return dom_ok or dow_ok  # day_or=True

    def times(self):
        return [time(h, m) for h in sorted(self.hours) for m in sorted(self.minutes)]


# ---------------------------------------------------------------- calendar
class OCal:
    def __init__(self, spec, provider):
        self.spec = spec
        self.provider = provider
        self._cache = {}

    def holidays(self, year):
        if year not in self._cache:
            merged = set()
            for ref in self.spec.holiday_calendars:
                merged |= set(self.provider.holidays(ref, year))  # may raise E405
            self._cache[year] = merged
        return self._cache[year]

    def is_bd(self, d):
        if d in self.spec.extra_working_days:
            return True
        if d in self.spec.extra_non_working_days:
            return False
        if d.weekday() in {int(w) for w in self.spec.weekend}:
            return False
        return d not in self.holidays(d.year)

    def roll(self, d, step):
        for i in range(1, MAX_ROLL + 1):
            c = d + timedelta(days=step * i)
            if self.is_bd(c):
                return c
        raise OracleError("E407")


# ---------------------------------------------------------------- local -> UTC by brute force
@lru_cache(maxsize=200_000)
def _offsets_near(tzkey, d: date):
    tz = ZoneInfo(tzkey)
    base = datetime.combine(d, time.min, tzinfo=UTC) - timedelta(hours=40)
    offs = set()
    for k in range(0, 4 * 24 * 2 + 1):  # every 30 min over 4 days
        offs.add((base + timedelta(minutes=30 * k)).astimezone(tz).utcoffset())
    return tuple(sorted(offs))


def _local_of(u, tz):
    return u.astimezone(tz).replace(tzinfo=None)


def preimages(W: datetime, tz: ZoneInfo):
    cands = sorted({(W - off).replace(tzinfo=UTC) for off in _offsets_near(tz.key, W.date())})
    return cands, [u for u in cands if _local_of(u, tz) == W]


def o_resolve(W, tz):
    cands, valid = preimages(W, tz)
    if valid:
        return min(valid)
    return max(cands)  # gap: shifted forward by the gap length


def o_classify(W, tz):
    _, valid = preimages(W, tz)
    return "normal" if len(valid) == 1 else ("ambiguous" if len(valid) > 1 else "gap")


# ---------------------------------------------------------------- releases
@dataclass(frozen=True)
class ORel:
    instant: datetime
    local_naive: datetime  # nominal wall time actually resolved (after roll)
    adjusted_from: date | None
    dst: str
    clamped: bool


def o_nominals(rule, ocal, d0, d1):
    """Nominals in generation order for local dates d0..d1 (policy applied)."""
    from freshcal.core.model import BusinessDaysSchedule, CronSchedule
    from freshcal.core.model import NonBusinessDayPolicy as P

    s = rule.schedule
    if isinstance(s, CronSchedule):
        c = Cron.parse(s.expression)
        times = c.times()
        d = d0
        while d <= d1:
            if c.day_matches(d):
                pol = s.on_non_business_day
                for t in times:
                    n = datetime.combine(d, t)
                    if pol is P.NONE or ocal.is_bd(d):
                        yield n, None, False
                    elif pol is P.SKIP:
                        pass
                    elif pol is P.FOLLOWING:
                        yield datetime.combine(ocal.roll(d, 1), t), d, False
                    else:
                        yield datetime.combine(ocal.roll(d, -1), t), d, False
            d += timedelta(days=1)
    elif isinstance(s, BusinessDaysSchedule):
        d = d0
        while d <= d1:
            if ocal.is_bd(d):
                yield datetime.combine(d, s.at), None, False
            d += timedelta(days=1)
    else:
        y, m = d0.year, d0.month
        while (y, m) <= (d1.year, d1.month):
            days = [date(y, m, k) for k in range(1, _cal.monthrange(y, m)[1] + 1)]
            bds = [x for x in days if ocal.is_bd(x)]
            if bds:
                n = s.business_day
                if n > 0:
                    pick, cl = bds[min(n, len(bds)) - 1], n > len(bds)
                else:
                    pick, cl = bds[max(n, -len(bds))], -n > len(bds)
                if d0 <= pick <= d1:
                    yield datetime.combine(pick, s.at), None, cl
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def o_floor(rule):
    if rule.active_from is None:
        return None
    return o_resolve(datetime.combine(rule.active_from, time.min), rule.schedule.timezone)


def _pad(rule):
    from freshcal.core.model import CronSchedule
    from freshcal.core.model import NonBusinessDayPolicy as P

    s = rule.schedule
    rolling = isinstance(s, CronSchedule) and s.on_non_business_day in (P.FOLLOWING, P.PRECEDING)
    return 40 if rolling else 3


def o_releases(rule, ocal, A, B, pad_days=None):
    """All releases with A <= instant <= B, sorted, first-nominal-wins metadata."""
    if pad_days is None:
        pad_days = _pad(rule)
    tz = rule.schedule.timezone
    d0 = A.astimezone(tz).date() - timedelta(days=pad_days)
    d1 = B.astimezone(tz).date() + timedelta(days=pad_days)
    floor = o_floor(rule)
    seen = {}
    for W, adj, cl in o_nominals(rule, ocal, d0, d1):
        u = o_resolve(W, tz)
        if floor is not None and u < floor:
            continue
        if A <= u <= B and u not in seen:
            seen[u] = ORel(u, W, adj, o_classify(W, tz), cl)
    return [seen[k] for k in sorted(seen)]


def o_first_after(rule, ocal, lo, hi):
    """Earliest release in [lo, hi] via growing contiguous windows (same result as o_releases(lo,hi)[0])."""
    step = timedelta(days=1)
    a = lo
    while a <= hi:
        b = min(a + step, hi)
        rs = o_releases(rule, ocal, a, b)
        if rs:
            return rs[0]
        a = b + timedelta(microseconds=1)
        step *= 2
    return None


def o_last_before(rule, ocal, lo, hi):
    step = timedelta(days=1)
    b = hi
    while b >= lo:
        a = max(b - step, lo)
        rs = o_releases(rule, ocal, a, b)
        if rs:
            return rs[-1]
        if a == lo:
            return None
        b = a - timedelta(microseconds=1)
        step *= 2
    return None


def o_take(rule, ocal, lo, hi, n):
    out = []
    step = timedelta(days=1)
    a = lo
    while a <= hi and len(out) < n:
        b = min(a + step, hi)
        out += o_releases(rule, ocal, a, b)
        a = b + timedelta(microseconds=1)
        step *= 2
    return out[:n]


# ---------------------------------------------------------------- verdict
@dataclass
class OResult:
    status: str
    release: datetime | None = None
    deadline: datetime | None = None
    next: datetime | None = None
    missed: int = 0
    pending: int = 0
    truncated: bool = False
    error: str | None = None
    next_known: bool = True  # False when the oracle could not look far enough
    last_known: bool = True
    release_meta: ORel | None = None


def o_evaluate(rule, observed_utc, now, provider, look=timedelta(days=400)):
    """Literal §3.7.2 with complete enumeration. `observed_utc` is an aware UTC instant or None.

    Context searches look `look` back/ahead instead of 1830 days (cost); *_known flags say
    whether a missing value is certain."""
    ocal = OCal(rule.calendar, provider)
    g = rule.grace
    tz = rule.schedule.timezone
    try:
        vu = rule.calendar.valid_until
        if vu is not None and now.astimezone(tz).date() > vu:
            return OResult("CONFIG_ERROR", error="E408")
        last = o_last_before(rule, ocal, now - min(look, H), now)
        nxt = o_first_after(rule, ocal, now + timedelta(microseconds=1), now + min(look, H))
        last_known = last is not None or look >= H
        next_known = nxt is not None or look >= H
        if last is None and nxt is None and look >= H:
            return OResult("CONFIG_ERROR", error="E209")
        res_ctx = dict(
            next=nxt.instant if nxt else None, next_known=next_known, last_known=last_known
        )
        if observed_utc is None and rule.active_from is None:
            return OResult(
                "NO_DATA",
                release=last.instant if last else None,
                deadline=(last.instant + g) if last else None,
                release_meta=last,
                **res_ctx,
            )
        if observed_utc is None:
            start, inclusive = o_floor(rule), True
        else:
            start, inclusive = observed_utc, False
        truncated = False
        if start >= now - H:
            lo = start if inclusive else start + timedelta(microseconds=1)
            U = o_first_after(rule, ocal, lo, now) if lo <= now else None
        else:
            recent = o_first_after(rule, ocal, now - H, now)
            if recent and now > recent.instant + g:
                U, truncated = recent, True
            else:
                lo = start if inclusive else start + timedelta(microseconds=1)
                U = o_first_after(rule, ocal, lo, start + H)
                if U is None:
                    return OResult("CONFIG_ERROR", error="E215")
        if U is None:
            return OResult(
                "ON_TIME",
                release=last.instant if last else None,
                deadline=(last.instant + g) if last else None,
                release_meta=last,
                **res_ctx,
            )
        unarrived = o_take(rule, ocal, U.instant, now, CAP)
        truncated = truncated or len(unarrived) == CAP
        missed = [r for r in unarrived if now > r.instant + g]
        pending = [r for r in unarrived if now <= r.instant + g]
        if missed:
            return OResult(
                "OVERDUE",
                release=missed[0].instant,
                deadline=missed[0].instant + g,
                missed=len(missed),
                pending=len(pending),
                truncated=truncated,
                release_meta=missed[0],
                **res_ctx,
            )
        return OResult(
            "NOT_DUE",
            release=pending[0].instant,
            deadline=pending[0].instant + g,
            pending=len(pending),
            truncated=truncated,
            release_meta=pending[0],
            **res_ctx,
        )
    except OracleError as e:
        return OResult("CONFIG_ERROR", error=e.code)
    except Exception as e:  # provider E405 etc.
        code = getattr(getattr(e, "issue", None), "code", None)
        if code:
            return OResult("CONFIG_ERROR", error=code)
        raise
