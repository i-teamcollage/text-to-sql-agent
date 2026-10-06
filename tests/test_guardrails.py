from datetime import date

import pytest
import sqlglot

from text_to_sql_agent.guardrails import MAX_ROWS, SecurityViolation, SqlError, guard

ALLOWED = ["orders", "order_items", "customers", "regions", "products", "categories", "refunds"]
RESTRICTED = ["customer_pii"]
PERSONAL = ["first_name", "last_name", "email", "phone", "street_address", "postal_code", "city"]


def guarded(sql, **kwargs):
    return guard(sql, allowed_tables=ALLOWED, restricted_tables=RESTRICTED, restricted_columns=PERSONAL, **kwargs)


def tables(sql):
    return guarded(sql).tables


def outer_limit(sql):
    return int(sqlglot.parse_one(sql, read="duckdb").args["limit"].expression.name)


# --- what should pass -------------------------------------------------------------------------


def test_a_plain_select_passes_and_keeps_its_tables():
    result = guarded("SELECT count(*) FROM orders WHERE status = 'delivered'")
    assert result.tables == ["orders"]


def test_a_with_query_passes_and_cte_names_are_not_treated_as_tables():
    result = guarded("WITH recent AS (SELECT * FROM orders) SELECT count(*) FROM recent")
    assert result.tables == ["orders"]


def test_a_join_lists_every_real_table():
    sql = (
        "SELECT c.category_name FROM order_items i JOIN products p ON p.product_id = i.product_id "
        "JOIN categories c ON c.category_id = p.category_id"
    )
    assert tables(sql) == ["categories", "order_items", "products"]


def test_a_union_of_selects_passes():
    assert guarded("SELECT 1 AS a FROM orders UNION ALL SELECT 2 AS a FROM refunds").tables == ["orders", "refunds"]


def test_duckdb_from_first_syntax_passes():
    assert tables("FROM orders SELECT count(*)") == ["orders"]


def test_a_query_without_tables_passes():
    assert guarded("SELECT 2 + 2 AS four").tables == []


# --- exactly one statement --------------------------------------------------------------------


def test_a_trailing_semicolon_is_fine():
    assert guarded("SELECT count(*) FROM orders;").tables == ["orders"]


def test_a_comment_after_the_trailing_semicolon_is_fine():
    assert guarded("SELECT count(*) FROM orders; -- all orders").tables == ["orders"]


def test_two_statements_are_refused():
    with pytest.raises(SecurityViolation, match="one statement"):
        guarded("SELECT 1 FROM orders; SELECT 2 FROM refunds")


def test_a_second_hidden_statement_is_refused():
    with pytest.raises(SecurityViolation, match="one statement"):
        guarded("SELECT 1 FROM orders; DROP TABLE orders")


def test_a_statement_smuggled_behind_a_backslash_is_refused():
    # DuckDB reads '\' as a complete string, so the text below is two statements to it.
    with pytest.raises(SecurityViolation, match="one statement"):
        guarded("SELECT '\\' ; SELECT * FROM customer_pii; --'")


# --- SELECT / WITH only -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "CREATE TABLE x AS SELECT 1",
        "ALTER TABLE orders ADD COLUMN x INT",
        "TRUNCATE orders",
    ],
)
def test_statements_that_change_data_are_refused(sql):
    with pytest.raises(SecurityViolation):
        guarded(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH 'other.db' AS other",
        "DETACH other",
        "COPY orders TO 'out.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "PRAGMA database_list",
        "SET threads = 1",
        "CALL pragma_version()",
        "EXPLAIN SELECT 1",
        "SHOW TABLES",
        "DESCRIBE customer_pii",
        "SUMMARIZE orders",
        "USE other",
        "CHECKPOINT",
        "BEGIN",
    ],
)
def test_configuration_extension_and_catalog_statements_are_refused(sql):
    with pytest.raises(SecurityViolation):
        guarded(sql)


@pytest.mark.parametrize("sql", ["PIVOT orders ON status USING count(*)", "VALUES (1), (2)"])
def test_other_read_only_forms_are_a_mistake_to_repair(sql):
    with pytest.raises(SqlError, match="SELECT"):
        guarded(sql)


# --- the restricted table and personal data ---------------------------------------------------


def test_the_pii_table_is_refused():
    with pytest.raises(SecurityViolation, match="customer_pii"):
        guarded("SELECT count(*) FROM customer_pii")


def test_the_pii_table_is_refused_even_when_joined_in():
    with pytest.raises(SecurityViolation, match="customer_pii"):
        guarded("SELECT o.order_id FROM orders o JOIN customer_pii p ON p.customer_id = o.customer_id")


def test_the_pii_table_is_refused_when_schema_qualified():
    with pytest.raises(SecurityViolation, match="customer_pii"):
        guarded("SELECT count(*) FROM main.customer_pii")


def test_the_pii_table_is_refused_whatever_the_letter_case():
    with pytest.raises(SecurityViolation, match="customer_pii"):
        guarded('SELECT count(*) FROM "Customer_PII"')


def test_a_cte_with_the_same_name_cannot_hide_the_pii_table_in_another_scope():
    # The CTE is only visible inside the subquery; the second customer_pii is the real table.
    sql = "SELECT * FROM (WITH customer_pii AS (SELECT 1 AS a) SELECT * FROM customer_pii) x, customer_pii y"
    with pytest.raises(SecurityViolation, match="customer_pii"):
        guarded(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT email FROM customers",
        "SELECT c.customer_id, c.phone FROM customers c",
        "SELECT count(*) FROM customers WHERE city = 'Paris'",
        "SELECT street_address, postal_code FROM customers",
    ],
)
def test_personal_data_columns_are_refused_wherever_they_are_asked_for(sql):
    with pytest.raises(SecurityViolation, match="personal data"):
        guarded(sql)


def test_a_personal_data_request_is_refused_even_if_a_table_is_also_wrong():
    # Security comes first: the made-up table would be a repair, the email column is a refusal.
    with pytest.raises(SecurityViolation, match="personal data"):
        guarded("SELECT email FROM customer_contacts")


# --- only allowed tables; unknown names are mistakes, not attacks -------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM duckdb_tables",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM system.main.duckdb_settings",
    ],
)
def test_system_catalogs_are_refused(sql):
    with pytest.raises(SecurityViolation, match="system catalog"):
        guarded(sql)


def test_an_unknown_table_is_a_mistake_to_repair_not_a_violation():
    with pytest.raises(SqlError, match="made_up_table"):
        guarded("SELECT * FROM made_up_table")


def test_a_cte_name_used_outside_its_scope_is_an_unknown_table():
    with pytest.raises(SqlError, match="recent"):
        guarded("SELECT * FROM (WITH recent AS (SELECT 1 AS a) SELECT * FROM recent) x, recent y")


# --- no table functions or file access --------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_parquet('s3://b/x.parquet')",
        "SELECT * FROM read_json('x.json')",
        "SELECT * FROM read_text('x.txt')",
        "SELECT * FROM glob('/etc/*')",
        "SELECT * FROM range(1000000)",
        "SELECT * FROM generate_series(1, 10)",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM sqlite_scan('x.db', 't')",
        "SELECT * FROM query('SELECT 1')",
        "SELECT * FROM unnest([1, 2, 3])",
        "SELECT * FROM orders CROSS JOIN unnest([1, 2]) u(x)",
    ],
)
def test_table_functions_are_refused(sql):
    with pytest.raises(SecurityViolation):
        guarded(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM 'data.csv'",
        'SELECT * FROM "orders.parquet"',
        "SELECT count(*) FROM 's3://exports-bucket/orders_2026.parquet'",
        "SELECT * FROM '/etc/passwd'",
    ],
)
def test_reading_a_file_by_its_name_is_refused(sql):
    with pytest.raises(SecurityViolation, match="file"):
        guarded(sql)


def test_a_file_read_hidden_in_a_join_is_refused():
    sql = "SELECT o.order_id FROM orders o, LATERAL (SELECT * FROM read_csv('x')) t"
    with pytest.raises(SecurityViolation):
        guarded(sql)


@pytest.mark.parametrize("sql", ["SELECT getenv('HOME')", "SELECT read_text('/etc/passwd')"])
def test_file_and_environment_scalar_functions_are_refused(sql):
    with pytest.raises(SecurityViolation):
        guarded(sql)


# --- the row limit, and the SQL that runs is the SQL written ------------------------------------


def test_a_missing_limit_is_added_on_its_own_line():
    sql = "SELECT order_id\nFROM orders\nORDER BY order_date"
    result = guarded(sql)
    assert result.limit_enforced
    assert result.sql == f"{sql}\nLIMIT {MAX_ROWS}"


def test_the_written_sql_is_kept_as_it_was_written():
    sql = "select o.order_id  -- the order\nfrom orders o\nwhere o.status <> 'cancelled'"
    assert guarded(sql).sql.startswith(sql)


def test_a_trailing_comment_cannot_swallow_the_limit():
    result = guarded("SELECT * FROM orders -- every order")
    assert result.sql == f"SELECT * FROM orders\nLIMIT {MAX_ROWS}"


def test_trailing_semicolons_are_removed_before_the_limit():
    assert guarded("SELECT * FROM orders;;\n").sql == f"SELECT * FROM orders\nLIMIT {MAX_ROWS}"


def test_a_small_limit_the_analyst_asked_for_is_kept():
    result = guarded("SELECT * FROM orders ORDER BY order_date DESC LIMIT 5")
    assert not result.limit_enforced
    assert result.sql == "SELECT * FROM orders ORDER BY order_date DESC LIMIT 5"


def test_fetch_first_within_the_cap_is_kept():
    result = guarded("SELECT * FROM orders ORDER BY order_date DESC FETCH FIRST 5 ROWS ONLY")
    assert not result.limit_enforced


def test_a_limit_above_the_cap_is_capped():
    result = guarded("SELECT * FROM orders ORDER BY order_date LIMIT 20000")
    assert result.limit_enforced
    assert outer_limit(result.sql) == MAX_ROWS


@pytest.mark.parametrize("sql", ["SELECT * FROM orders LIMIT 10%", "SELECT * FROM orders LIMIT 5 + 5"])
def test_a_limit_that_is_not_a_plain_number_is_capped(sql):
    result = guarded(sql)
    assert result.limit_enforced
    assert outer_limit(result.sql) == MAX_ROWS


def test_a_union_gets_one_limit_for_the_whole_result():
    result = guarded("SELECT 1 AS a FROM orders UNION ALL SELECT 2 AS a FROM refunds")
    assert outer_limit(result.sql) == MAX_ROWS


def test_the_guarded_sql_is_still_one_select():
    for sql in ["SELECT count(*) FROM orders", "WITH t AS (SELECT 1 AS a) SELECT * FROM t LIMIT 20000"]:
        statements = sqlglot.parse(guarded(sql).sql, read="duckdb")
        assert len(statements) == 1
        assert isinstance(statements[0], sqlglot.exp.Query)


# --- parse errors are repairable, not security violations --------------------------------------


def test_broken_sql_raises_a_repairable_error():
    with pytest.raises(SqlError):
        guarded("SELECT count( FROM orders WHERE")


def test_empty_input_raises_a_repairable_error():
    with pytest.raises(SqlError):
        guarded("   ")


# --- the warehouse is frozen on one day: the real clock gives wrong answers ---------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM orders WHERE order_date >= current_date - INTERVAL 30 DAY",
        "SELECT count(*) FROM orders WHERE order_date >= today() - 7",
        "SELECT now() AS t",
        "SELECT current_timestamp AS t",
        "SELECT get_current_timestamp() AS t",
        "SELECT localtimestamp AS t",
    ],
)
def test_reading_the_real_clock_is_a_mistake_to_repair_when_the_warehouse_has_a_fixed_today(sql):
    with pytest.raises(SqlError, match="2026-09-30"):
        guarded(sql, today=date(2026, 9, 30))


def test_the_real_clock_is_fine_when_no_fixed_today_is_given():
    assert guarded("SELECT count(*) FROM orders WHERE order_date >= current_date - 30").tables == ["orders"]


def test_dates_written_out_are_fine():
    sql = "SELECT count(*) FROM orders WHERE order_date >= DATE '2026-09-30' - INTERVAL 29 DAY"
    assert guarded(sql, today=date(2026, 9, 30)).tables == ["orders"]
