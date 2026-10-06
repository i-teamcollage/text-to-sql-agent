from pathlib import Path

import generate

from text_to_sql_agent.semantic import load_semantic

LAYER = load_semantic(Path(__file__).resolve().parents[1] / "semantic.toml")


def test_the_allowed_list_has_nine_tables_and_excludes_the_restricted_one():
    assert LAYER.restricted_tables == ["customer_pii"]
    assert "customer_pii" not in LAYER.allowed_tables
    assert len(LAYER.allowed_tables) == 9


def test_every_allowed_table_is_described_column_by_column():
    for name in LAYER.allowed_tables:
        described = list(LAYER.tables[name].columns)
        assert described == [column for column, _ in generate.TABLE_COLUMNS[name]], name


def test_the_restricted_columns_are_the_personal_data_in_the_restricted_table():
    personal = [column for column, _ in generate.TABLE_COLUMNS["customer_pii"] if column != "customer_id"]
    assert sorted(LAYER.restricted_columns) == sorted(personal)


def test_retrieval_finds_the_tables_a_revenue_question_needs():
    tables = LAYER.select_tables("What was gross revenue by product category in 2025?")
    assert {"orders", "order_items", "products", "categories"} <= set(tables)


def test_retrieval_adds_the_tables_needed_to_join():
    # order_items and regions only meet through orders and customers.
    tables = LAYER.select_tables("units sold per region")
    assert {"order_items", "orders", "customers", "regions"} <= set(tables)


def test_a_business_term_brings_the_tables_of_its_definition():
    tables = LAYER.select_tables("What's our net revenue for 2025?")
    assert {"orders", "order_items", "refunds"} <= set(tables)


def test_an_alias_of_a_business_term_counts_too():
    assert {"orders", "order_items"} <= set(LAYER.select_tables("AOV by platform for this year"))


def test_selection_is_deterministic_and_in_a_stable_order():
    question = "top 5 products by units in 2025"
    first = LAYER.select_tables(question)
    assert first == LAYER.select_tables(question)
    assert first == [name for name in LAYER.allowed_tables if name in first]


def test_the_schema_never_mentions_the_restricted_table_or_its_columns():
    prompt = LAYER.schema_prompt(LAYER.allowed_tables).lower()
    assert "customer_pii" not in prompt
    for column in ("first_name", "last_name", "street_address", "postal_code", "phone"):
        assert column not in prompt


def test_even_a_question_about_emails_never_surfaces_the_restricted_table():
    tables = LAYER.select_tables("email address and phone number of each customer")
    assert "customer_pii" not in tables
    assert "customer_pii" not in LAYER.schema_prompt(tables).lower()


def test_the_schema_describes_the_tables_it_was_given_and_how_they_join():
    prompt = LAYER.schema_prompt(["orders", "refunds"])
    assert "orders" in prompt and "refunds" in prompt
    assert "order_date" in prompt and "refund_date" in prompt
    assert "refunds.order_id = orders.order_id" in prompt
    assert "products" not in prompt


def test_definitions_are_always_available_in_full():
    definitions = LAYER.definitions_prompt().lower()
    for phrase in ["net revenue", "active customer", "average order value", "churned subscription", "churn rate"]:
        assert phrase in definitions


def test_the_conventions_pin_today():
    assert "2026-09-30" in LAYER.definitions_prompt()
    assert LAYER.as_of.isoformat() == "2026-09-30"


def test_the_definitions_never_mention_the_restricted_table():
    assert "customer_pii" not in LAYER.definitions_prompt().lower()
