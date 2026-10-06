"""The analysts' conventions, checked in code before a query runs.

The conventions are also in the prompt, but a small model does not always follow a long list. These
checks catch the mistakes that change an answer without causing an error: the wrong quarter, a sum
where a count was asked for, growth without the subtraction, a filter that hides the month LAG needs.
A problem goes back to the model as a repair, like a database error.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import sqlglot
from sqlglot import expressions as exp

_COUNTED = r"orders|refunds|customers|subscriptions|subscribers|sign-?ups|products|campaigns"
_HOW_MANY = re.compile(rf"\b(?:how many|number of)\s+(?:[\w-]+\s+){{0,2}}?({_COUNTED})\b", re.I)
_LAST_DAYS = re.compile(r"\b(?:last|past)\s+(\d+)\s+days\b", re.I)


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _periods(question: str, today: date) -> list[tuple[str, date, date, bool]]:
    """(phrase, start, end, end_required) for every relative period the question names."""
    text = question.lower()
    this_quarter = _quarter_start(today)
    last_quarter_end = this_quarter - timedelta(days=1)
    this_month = today.replace(day=1)
    last_month_end = this_month - timedelta(days=1)
    twelve_months = date(today.year - 1, today.month + 1, 1) if today.month < 12 else date(today.year, 1, 1)
    candidates = [
        ("last quarter", _quarter_start(last_quarter_end), last_quarter_end, True),
        ("this quarter", this_quarter, today, False),
        ("last month", last_month_end.replace(day=1), last_month_end, True),
        ("this month", this_month, today, False),
        ("last year", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), True),
        ("this year", date(today.year, 1, 1), today, False),
        ("year to date", date(today.year, 1, 1), today, False),
        ("last 12 months", twelve_months, today, False),
        ("past week", today - timedelta(days=6), today, False),
    ]
    found = [period for period in candidates if re.search(rf"\b{period[0]}\b", text)]
    for match in _LAST_DAYS.finditer(question):
        days = int(match.group(1))
        found.append((match.group(0).lower(), today - timedelta(days=days - 1), today, False))
    return found


def _period_problem(question: str, sql: str, today: date) -> str | None:
    for phrase, start, end, end_required in _periods(question, today):
        has_start = start.isoformat() in sql
        has_end = end.isoformat() in sql or (end + timedelta(days=1)).isoformat() in sql
        if not has_start or (end_required and not has_end):
            return (
                f"The question says '{phrase}', which is {start.isoformat()} to {end.isoformat()}. "
                "The query does not use that date range. Write those dates out and filter on them."
            )
    return None


def _count_problem(question: str, sql: str) -> str | None:
    match = _HOW_MANY.search(question)
    if match and "count(" not in sql.lower().replace(" ", ""):
        return (
            f"The question asks how many {match.group(1)}: count them with COUNT "
            "(or COUNT(DISTINCT ...)), not a sum of amounts."
        )
    return None


def _growth_problem(question: str, sql: str) -> str | None:
    if not re.search(r"\bgrowth\b", question, re.I):
        return None
    code = re.sub(r"--[^\n]*", "", re.sub(r"'[^']*'", "''", sql))
    if "/" in code and not re.search(r"[\w)\]]\s*-\s*[\w(]", code):
        return (
            "Growth must subtract first: (current - previous) * 100.0 / previous. "
            "The query divides without subtracting, which gives a ratio, not growth."
        )
    return None


def _lag_problem(sql: str) -> str | None:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return None
    for select in tree.find_all(exp.Select):
        windows = [
            window
            for projection in select.expressions
            for window in projection.find_all(exp.Window)
            if isinstance(window.this, (exp.Lag, exp.Lead))
        ]
        if windows and select.args.get("where") is not None:
            return (
                "LAG and a WHERE are in the same SELECT. The WHERE runs first and removes the earlier months "
                "LAG needs, so the first month has no previous value. Compute LAG over all the months in a "
                "CTE or subquery and filter the months outside it, or use QUALIFY."
            )
    return None


def convention_problem(question: str, sql: str, today: date) -> str | None:
    """The first convention the query breaks, as a message for the model, or None."""
    return (
        _period_problem(question, sql, today)
        or _count_problem(question, sql)
        or _growth_problem(question, sql)
        or _lag_problem(sql)
    )
