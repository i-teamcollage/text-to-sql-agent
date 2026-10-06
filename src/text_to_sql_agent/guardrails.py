"""The guardrails: checks on the SQL the model wrote, in code, before anything touches the database.

Two kinds of problem are told apart, because the agent treats them differently:

- SqlError: a mistake the model can fix. The SQL does not parse, names a table that does not exist,
  or is a read-only form other than SELECT. It is sent back to the model for repair.
- SecurityViolation: the SQL asks for something it must never do: change data or settings, run more
  than one statement, read the restricted table or personal data, read a file or a system catalog,
  or call a table function. It is refused at once and never repaired.

Every check works on the parse tree from sqlglot, so whitespace, comments, letter case or quoting
cannot slip a forbidden construct past a string search. Security checks run before the checks for
mistakes, so a query with both is refused rather than repaired.

The SQL that runs is the SQL the model wrote, with at most one change: a LIMIT of at most 10,000 rows,
added on its own line when missing. The database layer (database.py) then checks the same text with
DuckDB's own parser and query plan, on a read-only connection; no layer is trusted on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import TokenType

MAX_ROWS = 10_000
DIALECT = "duckdb"

# Scalar functions that reach outside the database. Table functions (read_csv, glob, range, ...) are
# caught separately: any function used where a table belongs is refused.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "getenv",
        "read_text",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "query",
        "query_table",
        "glob",
    }
)
# Functions that read the real clock. The warehouse stops on a fixed day, so they give wrong answers.
CLOCK_NODES = (exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentTime, exp.Localtimestamp)
CLOCK_FUNCTIONS = frozenset({"now", "get_current_timestamp", "transaction_timestamp", "current_localtime"})
SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog"})
SYSTEM_PREFIXES = ("duckdb_", "pg_", "sqlite_", "information_schema")
FILE_CHARACTERS = frozenset("./\\:*?")
QUERY_ROOTS = (exp.Select, exp.SetOperation, exp.Subquery)
READ_ONLY_FORMS = (exp.Pivot, exp.Values)


class GuardrailError(Exception):
    """Base class for a query the guardrails will not run."""


class SqlError(GuardrailError):
    """A mistake in the SQL. The model is asked to fix it (a repair)."""


class SecurityViolation(GuardrailError):
    """The SQL asks for something forbidden. It is refused at once, never repaired."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str  # the SQL to run: what the model wrote, plus the row limit when it was missing
    tables: list[str]  # the real tables it reads, lower-cased and sorted
    limit_enforced: bool  # True when the guardrails added or lowered the LIMIT


def _parse(sql: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except (ParseError, TokenError) as error:
        raise SqlError(f"the SQL could not be parsed: {str(error).splitlines()[0]}") from error
    # A trailing semicolon followed by a comment comes back as a Semicolon pseudo-statement.
    statements = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
    if not statements:
        raise SqlError("no SQL statement was found")
    if len(statements) > 1:
        raise SecurityViolation("more than one statement: exactly one SELECT is allowed")
    return statements[0]


def _require_select(root: exp.Expression) -> None:
    if isinstance(root, QUERY_ROOTS):
        return
    if isinstance(root, READ_ONLY_FORMS):
        raise SqlError("only a single SELECT (or WITH ... SELECT) may run: write this as a SELECT")
    if isinstance(root, exp.Command):
        keyword = (root.this or "statement").strip().upper()
        raise SecurityViolation(f"{keyword} is not allowed: only a single SELECT may run")
    name = root.key.upper() if root.key else type(root).__name__.upper()
    raise SecurityViolation(f"{name} is not allowed: only a single SELECT may run")


def _function_name(function: exp.Func) -> str:
    # An unknown function is parsed as Anonymous, which keeps its real name in `.name`; a function
    # sqlglot knows (read_csv -> ReadCSV, range -> GenerateSeries) exposes it through sql_name().
    name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
    return name.lower()


def _cte_in_scope(table: exp.Table, name: str) -> bool:
    """True when a WITH clause around this table defines a CTE of that name."""
    node = table.parent
    while node is not None:
        with_clause = node.args.get("with_")
        if with_clause is not None and any(cte.alias_or_name.lower() == name for cte in with_clause.expressions):
            return True
        node = node.parent
    return False


def _security_checks(root: exp.Expression, restricted: set[str], personal: set[str]) -> None:
    for table in root.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            raise SecurityViolation(f"the table function {_function_name(table.this)} is not allowed")
        name = table.name.lower()
        if name in restricted:
            raise SecurityViolation(f"the restricted table {name} may never be read")
        if FILE_CHARACTERS & set(name):
            raise SecurityViolation(f"reading files is not allowed ({table.name!r})")
        schema = table.db.lower()
        if schema in SYSTEM_SCHEMAS or name.startswith(SYSTEM_PREFIXES):
            raise SecurityViolation(f"the system catalog is not allowed ({table.sql(dialect=DIALECT)})")
    for unnest in root.find_all(exp.Unnest):
        if isinstance(unnest.parent, (exp.From, exp.Join, exp.Lateral)):
            raise SecurityViolation("the table function unnest is not allowed")
    for function in root.find_all(exp.Func):
        name = _function_name(function)
        if name in FORBIDDEN_FUNCTIONS or name.endswith("_scan"):
            raise SecurityViolation(f"the function {name} is not allowed")
    for column in root.find_all(exp.Column):
        if column.name.lower() in personal:
            raise SecurityViolation(f"personal data is restricted (column {column.name})")


def _tables(root: exp.Expression, allowed: set[str]) -> list[str]:
    used: set[str] = set()
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        qualified = bool(table.args.get("db") or table.args.get("catalog"))
        if not qualified and _cte_in_scope(table, name):
            continue
        if name not in allowed:
            raise SqlError(f"there is no table named {table.name} in the warehouse: use only the tables given")
        used.add(name)
    return sorted(used)


def _check_clock(root: exp.Expression, today: date) -> None:
    for function in root.find_all(exp.Func):
        if isinstance(function, CLOCK_NODES) or _function_name(function) in CLOCK_FUNCTIONS:
            raise SqlError(
                f"do not read the clock ({function.sql(dialect=DIALECT)}): the data ends on {today.isoformat()}, "
                "which counts as today, so write the dates out"
            )


def _limit_value(limit: exp.Expression | None) -> int | None:
    """The row count of a plain LIMIT n or FETCH FIRST n ROWS ONLY; None for anything else."""
    if limit is None:
        return None
    options = limit.args.get("limit_options")
    if options is not None and (options.args.get("percent") or options.args.get("with_ties")):
        return None  # LIMIT 10% or WITH TIES: the row count is not a plain number
    value = limit.args.get("count") if isinstance(limit, exp.Fetch) else limit.args.get("expression")
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.name)
    return None


def _body(sql: str) -> str:
    """The SQL up to its last real token: trailing semicolons and comments are dropped."""
    tokens = [token for token in sqlglot.tokenize(sql, read=DIALECT) if token.token_type != TokenType.SEMICOLON]
    return sql[: tokens[-1].end + 1]


def _apply_limit(sql: str, root: exp.Expression, max_rows: int) -> tuple[str, bool]:
    body = _body(sql)
    limit = root.args.get("limit")
    if limit is None:
        return f"{body}\nLIMIT {max_rows}", True
    value = _limit_value(limit)
    if value is not None and value <= max_rows:
        return body, False
    return f"SELECT * FROM (\n{body}\n) AS capped\nLIMIT {max_rows}", True


def guard(
    sql: str,
    *,
    allowed_tables: list[str],
    restricted_tables: list[str],
    restricted_columns: list[str] = (),
    today: date | None = None,
    max_rows: int = MAX_ROWS,
) -> GuardedQuery:
    """Check one piece of model SQL and return the query to run, row limit applied, or raise.

    When the warehouse has a fixed "today", a query that reads the real clock is a mistake to repair.
    """
    root = _parse(sql)
    _require_select(root)
    _security_checks(
        root,
        restricted={name.lower() for name in restricted_tables},
        personal={name.lower() for name in restricted_columns},
    )
    tables = _tables(root, {name.lower() for name in allowed_tables})
    if today is not None:
        _check_clock(root, today)
    limited, enforced = _apply_limit(sql, root, max_rows)
    return GuardedQuery(sql=limited, tables=tables, limit_enforced=enforced)
