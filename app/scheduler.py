"""A tiny scheduler: runs jobs at fixed local times (Europe/London) without extra dependencies."""
import logging
import time as _time
from datetime import datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("scheduler")


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h), int(m))


def next_run(now: datetime, times: list[str], tz: str) -> datetime:
    """Next scheduled moment strictly after `now` (aware datetimes)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    candidates = []
    for day in (0, 1):
        d = (local + timedelta(days=day)).date()
        for t in sorted(map(_parse, times)):
            at = datetime.combine(d, t, tzinfo=zone)
            if at > local:
                candidates.append(at)
    return min(candidates)


def run_forever(jobs: dict[str, tuple[list[str], Callable[[], None]]], tz: str):
    """jobs: name -> (times, function). Each job runs at each of its times; errors are logged, not fatal."""
    while True:
        now = datetime.now(ZoneInfo(tz))
        upcoming = sorted((next_run(now, times, tz), name) for name, (times, _) in jobs.items())
        at = upcoming[0][0]
        due = [name for t, name in upcoming if t == at]
        _time.sleep(max(0.0, (at - datetime.now(ZoneInfo(tz))).total_seconds()))
        for name in due:
            try:
                log.info("running %s", name)
                jobs[name][1]()
            except Exception:
                log.exception("job %s failed", name)
