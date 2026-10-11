"""Shared XAUUSD M15 gap classification used by validation and Stage 1."""

from datetime import date, datetime, timedelta
from typing import Set


CONTIGUOUS = "CONTIGUOUS"
WEEKEND = "WEEKEND_OR_WEEKEND_CROSSING"
DAILY_BREAK = "SCHEDULED_DAILY_METALS_BREAK"
HOLIDAY_BREAK = "SCHEDULED_HOLIDAY_BREAK"
UNEXPECTED = "UNEXPECTED"


def crosses_weekend(start: datetime, end: datetime) -> bool:
    cursor = start.date()
    while cursor <= end.date():
        if cursor.weekday() >= 5:
            return True
        cursor += timedelta(days=1)
    return False


def is_scheduled_metals_break(start: datetime, end: datetime) -> bool:
    if end - start != timedelta(minutes=75):
        return False
    return (start.hour, start.minute, end.hour, end.minute) in {
        (20, 45, 22, 0),
        (21, 45, 23, 0),
    }


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def scheduled_us_market_dates(year: int) -> Set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    independence = date(year, 7, 4)
    christmas = date(year, 12, 25)
    new_year = date(year, 1, 1)
    dates = {
        _observed(new_year),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _observed(independence),
        _nth_weekday(year, 9, 0, 1),
        thanksgiving,
        thanksgiving + timedelta(days=1),
        _observed(christmas),
    }
    if year >= 2022:
        dates.add(_observed(date(year, 6, 19)))
    # Common CME metals early-close dates.
    for candidate in (
        independence - timedelta(days=1),
        christmas - timedelta(days=1),
        new_year - timedelta(days=1),
    ):
        if candidate.weekday() < 5:
            dates.add(candidate)
    return dates


def is_scheduled_holiday_break(start: datetime, end: datetime) -> bool:
    cursor = start.date()
    while cursor <= end.date():
        for year in {cursor.year - 1, cursor.year, cursor.year + 1}:
            if cursor in scheduled_us_market_dates(year):
                return True
        cursor += timedelta(days=1)
    return False


def classify_gap(start: datetime, end: datetime) -> str:
    if end - start == timedelta(minutes=15):
        return CONTIGUOUS
    if crosses_weekend(start, end):
        return WEEKEND
    if is_scheduled_metals_break(start, end):
        return DAILY_BREAK
    if is_scheduled_holiday_break(start, end):
        return HOLIDAY_BREAK
    return UNEXPECTED
