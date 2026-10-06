"""The database layer: a locked-down connection, a second opinion from DuckDB, and a hard timeout.

Defence in depth sits here. The guardrails (guardrails.py) already refuse anything that is not one
plain SELECT on the allowed tables, but this layer does not take their word for it:

- The connection is read-only, with external file access off, extension loading off and the
  configuration locked. Writes, ATTACH, COPY, file reads and setting changes fail in the engine.
- DuckDB's own parser must see exactly one SELECT, and DuckDB's own query plan (EXPLAIN) may scan
  only the allowed tables and no table function. If sqlglot and DuckDB ever read a piece of SQL
  differently, the plan is what actually runs, so the plan is what is checked.
- The cost check reads the same plan and refuses a runaway, such as a cross join between two large
  tables, before it runs.
- Execution gets at most ten seconds; when it overruns, the query is interrupted.

A plan that touches something forbidden is a security violation and is never repaired. A query that
fails, overruns or would run away is an error the model may be asked to repair.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import duckdb

from .guardrails import SecurityViolation

TIMEOUT_SECONDS = 10.0
# A join with no usable matching condition (a cross product or nested loop) that would compare more
# row pairs than this is a runaway. Every order against a one-row total is fine; orders x order_items
# is not.
CROSS_PRODUCT_LIMIT = 10_000_000
# No step of a plan should expect to handle more rows than this.
HARD_ROW_LIMIT = 50_000_000
_CROSS_NODES = {"CROSS_PRODUCT", "NESTED_LOOP_JOIN", "BLOCKWISE_NL_JOIN"}

_READ_ONLY_CONFIG = {
    "enable_external_access": False,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    # One thread: rows that tie come back in the same order on every machine, so a replay of the
    # stored run on another computer matches the recording. The warehouse is small enough not to mind.
    "threads": 1,
    "lock_configuration": True,
}


class DatabaseError(Exception):
    """A query the database layer would not, or could not, complete. Repairable."""


class QueryError(DatabaseError):
    """DuckDB rejected or failed the query (bad column, a blocked write, a disabled feature)."""


class QueryTimeout(DatabaseError):
    """The query ran longer than the timeout and was interrupted."""


class RunawayQuery(DatabaseError):
    """The query plan is far too expensive to run (for example a cross join of two large tables)."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    seconds: float
    row_count: int


def connect(path: str) -> duckdb.DuckDBPyConnection:
    """Open the warehouse read-only, with external access disabled and the configuration locked."""
    return duckdb.connect(str(path), read_only=True, config=dict(_READ_ONLY_CONFIG))


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__


def _estimate(node: dict) -> int:
    raw = node.get("extra_info", {}).get("Estimated Cardinality")
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _walk(node: dict, allowed: set[str]) -> None:
    info = node.get("extra_info", {})
    function = info.get("Function")
    if function:
        raise SecurityViolation(f"the plan reads through the table function {str(function).lower()}")
    table = info.get("Table")
    if table:
        name = str(table).split(".")[-1].lower()
        if name not in allowed:
            raise SecurityViolation(f"the plan reads the table {name}, which is not allowed")
    if _estimate(node) > HARD_ROW_LIMIT:
        raise RunawayQuery(f"the plan expects about {_estimate(node):,} rows at one step, which is too many")
    children = node.get("children", [])
    if node.get("name", "").strip() in _CROSS_NODES:
        pairs = 1
        for child in children:
            pairs *= max(_estimate(child), 1)
        if pairs > CROSS_PRODUCT_LIMIT:
            raise RunawayQuery(
                "the query joins large tables without a matching condition (a cross join); "
                "add a join condition or a filter"
            )
    for child in children:
        _walk(child, allowed)


def _one_select(sql: str) -> None:
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as error:
        raise QueryError(f"the query could not be parsed: {_first_line(error)}") from error
    if len(statements) != 1:
        raise SecurityViolation("DuckDB reads more than one statement here: exactly one SELECT is allowed")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise SecurityViolation(f"DuckDB reads a {statements[0].type.name} statement: only a SELECT may run")


def _references(node, tables: list[tuple[str, str]], functions: list[str], ctes: set[str]) -> None:
    """Collect the tables, table functions and CTE names in DuckDB's parse tree."""
    if isinstance(node, dict):
        kind = node.get("type")
        if kind == "BASE_TABLE":
            tables.append((str(node.get("schema_name") or ""), str(node.get("table_name") or "")))
        elif kind == "TABLE_FUNCTION":
            function = node.get("function") or {}
            functions.append(str(function.get("function_name") or "unknown"))
        for item in (node.get("cte_map") or {}).get("map", []):
            ctes.add(str(item.get("key", "")).lower())
        for value in node.values():
            _references(value, tables, functions, ctes)
    elif isinstance(node, list):
        for value in node:
            _references(value, tables, functions, ctes)


def _parsed_tables(connection, sql: str, allowed: set[str], restricted: set[str]) -> None:
    """DuckDB's own parse tree must name only allowed tables (or CTEs), and no table function."""
    try:
        tree = json.loads(connection.cursor().execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    except duckdb.Error as error:
        raise QueryError(f"the query could not be parsed: {_first_line(error)}") from error
    if tree.get("error"):
        if tree.get("error_type") == "parser":
            raise QueryError(f"the query could not be parsed: {tree.get('error_message', '')}")
        raise SecurityViolation("DuckDB does not read this as a plain SELECT")
    tables: list[tuple[str, str]] = []
    functions: list[str] = []
    ctes: set[str] = set()
    _references(tree, tables, functions, ctes)
    if functions:
        raise SecurityViolation(f"the table function {functions[0].lower()} is not allowed")
    for schema, table in tables:
        name = table.lower()
        if name in restricted:
            raise SecurityViolation(f"the restricted table {name} may never be read")
        if name not in allowed and not (name in ctes and not schema):
            raise SecurityViolation(f"DuckDB reads the table {table}, which is not allowed")


def check_plan(
    connection: duckdb.DuckDBPyConnection, sql: str, *, allowed_tables: list[str], restricted_tables: list[str]
) -> None:
    """Check the SQL with DuckDB's own parser and plan before it runs.

    Raises SecurityViolation (never repaired), RunawayQuery or QueryError (both repairable).
    """
    allowed = {name.lower() for name in allowed_tables}
    _one_select(sql)
    _parsed_tables(connection, sql, allowed, {name.lower() for name in restricted_tables})
    try:
        rows = connection.cursor().execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()
    except duckdb.Error as error:
        raise QueryError(f"the query could not be planned: {_first_line(error)}") from error
    plan_json = next((value for key, value in rows if key == "physical_plan"), None)
    for root in json.loads(plan_json) if plan_json else []:
        _walk(root, allowed)


def run(connection: duckdb.DuckDBPyConnection, sql: str, timeout: float = TIMEOUT_SECONDS) -> QueryResult:
    """Run a SELECT on its own cursor with a hard timeout. Raises QueryTimeout or QueryError."""
    cursor = connection.cursor()
    timer = threading.Timer(timeout, cursor.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        cursor.execute(sql)
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
    except duckdb.InterruptException as error:
        raise QueryTimeout(f"the query ran longer than {timeout:g} seconds and was stopped") from error
    except duckdb.Error as error:
        raise QueryError(_first_line(error)) from error
    finally:
        timer.cancel()
        cursor.close()
    seconds = round(time.perf_counter() - started, 3)
    return QueryResult(columns=columns, rows=rows, seconds=seconds, row_count=len(rows))
