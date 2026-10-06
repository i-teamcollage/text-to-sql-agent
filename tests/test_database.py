import time

import pytest

from text_to_sql_agent.database import (
    QueryError,
    QueryTimeout,
    RunawayQuery,
    check_plan,
    connect,
    run,
)
from text_to_sql_agent.guardrails import SecurityViolation

ALLOWED = [
    "customers",
    "regions",
    "products",
    "categories",
    "orders",
    "order_items",
    "refunds",
    "subscriptions",
    "marketing_campaigns",
]


def plan(connection, sql):
    check_plan(connection, sql, allowed_tables=ALLOWED, restricted_tables=["customer_pii"])


# --- defence in depth: the connection itself -----------------------------------------------------


def test_a_select_runs_and_returns_columns_and_rows(warehouse_db):
    with connect(warehouse_db) as connection:
        result = run(connection, "SELECT count(*) AS n FROM orders")
    assert result.columns == ["n"]
    assert result.row_count == 1
    assert result.rows[0][0] > 10000


def test_a_write_cannot_run_even_if_it_reaches_the_database(warehouse_db):
    # The connection is read-only, so a write fails even with no guardrail in front of it.
    with connect(warehouse_db) as connection, pytest.raises(QueryError):
        run(connection, "DELETE FROM orders")


def test_file_access_is_disabled_on_the_connection(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(QueryError):
        run(connection, "SELECT * FROM read_csv('/etc/hosts')")


def test_the_connection_cannot_be_reconfigured(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(QueryError):
        run(connection, "SET enable_external_access = true")


def test_a_broken_query_raises_a_repairable_error(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(QueryError):
        run(connection, "SELECT no_such_column FROM orders")


# --- DuckDB's own parser and plan: a second opinion on what the guardrails saw ---------------------


def test_a_normal_join_passes_the_plan_check(warehouse_db):
    with connect(warehouse_db) as connection:
        plan(connection, "SELECT count(*) FROM orders o JOIN order_items i ON i.order_id = o.order_id")


def test_ctes_and_subqueries_pass_the_plan_check(warehouse_db):
    sql = (
        "WITH paid_back AS (SELECT order_id, SUM(amount) AS refunded FROM refunds GROUP BY order_id) "
        "SELECT count(*) FROM orders o LEFT JOIN paid_back p ON p.order_id = o.order_id "
        "WHERE o.customer_id IN (SELECT customer_id FROM customers WHERE segment = 'business')"
    )
    with connect(warehouse_db) as connection:
        plan(connection, sql)


def test_the_plan_check_refuses_the_restricted_table_even_if_the_guardrails_missed_it(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="customer_pii"):
        plan(connection, "SELECT count(*) FROM customer_pii")


def test_the_plan_check_refuses_system_catalogs(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation):
        plan(connection, "SELECT * FROM information_schema.tables")


def test_the_plan_check_refuses_table_functions(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="range"):
        plan(connection, "SELECT * FROM range(5)")


def test_duckdb_must_also_see_exactly_one_statement(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="one statement"):
        plan(connection, "SELECT 1 AS a; SELECT 2 AS b")


def test_duckdb_must_also_see_a_select(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="only a SELECT"):
        plan(connection, "DELETE FROM orders")


def test_a_pragma_that_duckdb_rewrites_into_a_select_is_still_refused(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="plain SELECT"):
        plan(connection, "PRAGMA database_list")


def test_a_cte_cannot_borrow_the_name_of_the_restricted_table(warehouse_db):
    sql = "WITH customer_pii AS (SELECT 1 AS a) SELECT * FROM customer_pii"
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="customer_pii"):
        plan(connection, sql)


def test_a_file_read_by_name_is_refused_by_duckdb_too(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(SecurityViolation, match="data.csv"):
        plan(connection, "SELECT * FROM 'data.csv'")


def test_a_query_that_cannot_be_planned_is_a_repairable_error(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(QueryError):
        plan(connection, "SELECT no_such_column FROM orders")


# --- the cost check -------------------------------------------------------------------------------


def test_a_cross_join_without_a_predicate_is_rejected(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(RunawayQuery, match="cross join"):
        plan(connection, "SELECT count(*) FROM orders, order_items")


def test_an_explicit_cross_join_is_rejected(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(RunawayQuery):
        plan(connection, "SELECT * FROM orders o JOIN order_items i ON true")


def test_a_join_on_a_condition_that_matches_almost_everything_is_rejected(warehouse_db):
    with connect(warehouse_db) as connection, pytest.raises(RunawayQuery):
        plan(connection, "SELECT count(*) FROM orders a JOIN orders b ON a.platform = b.platform")


def test_a_small_cross_join_is_not_a_runaway(warehouse_db):
    # Every order against a one-row total is cheap and common (share of total).
    sql = "SELECT o.order_id FROM orders o CROSS JOIN (SELECT count(*) AS n FROM orders) t"
    with connect(warehouse_db) as connection:
        plan(connection, sql)


def test_a_date_range_join_to_the_campaigns_is_not_a_runaway(warehouse_db):
    sql = (
        "SELECT m.campaign_name, count(*) FROM orders o "
        "JOIN marketing_campaigns m ON o.order_date BETWEEN m.start_date AND m.end_date GROUP BY 1"
    )
    with connect(warehouse_db) as connection:
        plan(connection, sql)


def test_a_scalar_subquery_is_not_mistaken_for_a_cross_join(warehouse_db):
    sql = "SELECT order_id, (SELECT max(amount) FROM refunds) AS top_refund FROM orders"
    with connect(warehouse_db) as connection:
        plan(connection, sql)


# --- the timeout ------------------------------------------------------------------------------------


def test_a_slow_query_is_interrupted_by_the_timeout(warehouse_db):
    started = time.perf_counter()
    with connect(warehouse_db) as connection, pytest.raises(QueryTimeout):
        run(connection, "SELECT count(*) FROM orders a, orders b, orders c", timeout=0.5)
    assert time.perf_counter() - started < 10


def test_the_connection_still_works_after_a_timeout(warehouse_db):
    with connect(warehouse_db) as connection:
        with pytest.raises(QueryTimeout):
            run(connection, "SELECT count(*) FROM orders a, orders b, orders c", timeout=0.3)
        assert run(connection, "SELECT 1 AS one").rows == [(1,)]


def test_a_quick_query_reports_its_time(warehouse_db):
    with connect(warehouse_db) as connection:
        result = run(connection, "SELECT count(*) FROM orders", timeout=10)
    assert 0 <= result.seconds < 10
