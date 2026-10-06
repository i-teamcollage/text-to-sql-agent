from collections import defaultdict
from datetime import date
from decimal import Decimal

import generate

TABLES = generate.build_tables()
START, END = date(2024, 1, 1), date(2026, 9, 30)


def rows(name):
    table = TABLES[name]
    names = [column for column, _ in table.columns]
    return [dict(zip(names, row, strict=True)) for row in table.rows]


def by_id(name, key):
    return {row[key]: row for row in rows(name)}


def test_the_warehouse_has_the_ten_promised_tables():
    assert set(TABLES) == {
        "customers",
        "customer_pii",
        "regions",
        "products",
        "categories",
        "orders",
        "order_items",
        "refunds",
        "subscriptions",
        "marketing_campaigns",
    }


def test_same_seed_gives_the_same_warehouse():
    again = generate.build_tables()
    assert all(again[name].rows == TABLES[name].rows for name in TABLES)


def test_another_seed_gives_another_warehouse():
    assert generate.build_tables(seed=1)["orders"].rows != TABLES["orders"].rows


def test_every_date_falls_inside_the_warehouse_window():
    for name, table in TABLES.items():
        for index, (column, kind) in enumerate(table.columns):
            if kind != "DATE":
                continue
            values = [row[index] for row in table.rows if row[index] is not None]
            assert START <= min(values) and max(values) <= END, (name, column)


def test_the_big_tables_need_the_row_limit():
    for name in ("customers", "customer_pii", "orders", "order_items"):
        assert len(TABLES[name].rows) > 10_000, name


def test_every_foreign_key_points_at_an_existing_row():
    keys = {
        ("customers", "region_id"): ("regions", "region_id"),
        ("customer_pii", "customer_id"): ("customers", "customer_id"),
        ("products", "category_id"): ("categories", "category_id"),
        ("orders", "customer_id"): ("customers", "customer_id"),
        ("orders", "campaign_id"): ("marketing_campaigns", "campaign_id"),
        ("order_items", "order_id"): ("orders", "order_id"),
        ("order_items", "product_id"): ("products", "product_id"),
        ("refunds", "order_id"): ("orders", "order_id"),
        ("subscriptions", "customer_id"): ("customers", "customer_id"),
    }
    for (table, column), (parent, key) in keys.items():
        known = set(by_id(parent, key))
        values = {row[column] for row in rows(table) if row[column] is not None}
        assert values <= known, (table, column)


def test_primary_keys_are_unique():
    for name, table in TABLES.items():
        first = [row[0] for row in table.rows]
        assert len(first) == len(set(first)), name


def test_each_customer_has_one_row_of_made_up_personal_data():
    pii = rows("customer_pii")
    assert [row["customer_id"] for row in pii] == [row["customer_id"] for row in rows("customers")]
    assert all(row["email"].endswith(("@example.com", "@example.org", "@example.net")) for row in pii)
    assert all(row["phone"].startswith("+44 7700 900") for row in pii)


def test_customers_sign_up_only_where_the_shop_had_launched():
    regions = by_id("regions", "region_id")
    for customer in rows("customers"):
        region = regions[customer["region_id"]]
        assert customer["signup_date"] >= region["launched_on"]
        assert customer["country_code"] in region["countries"].split(", ")


def test_customers_order_only_after_signing_up():
    customers = by_id("customers", "customer_id")
    for order in rows("orders"):
        assert order["order_date"] >= customers[order["customer_id"]]["signup_date"]


def test_orders_contain_only_products_on_sale_that_day():
    products = by_id("products", "product_id")
    orders = by_id("orders", "order_id")
    for item in rows("order_items"):
        product, day = products[item["product_id"]], orders[item["order_id"]]["order_date"]
        assert product["launched_on"] <= day
        assert product["discontinued_on"] is None or day < product["discontinued_on"]


def test_order_lines_are_charged_at_the_price_of_their_year():
    factor = {2024: Decimal("0.94"), 2025: Decimal("0.97"), 2026: Decimal("1.00")}
    products = by_id("products", "product_id")
    orders = by_id("orders", "order_id")
    for item in rows("order_items"):
        year = orders[item["order_id"]]["order_date"].year
        expected = (products[item["product_id"]]["list_price"] * factor[year]).quantize(Decimal("0.01"))
        assert item["unit_price"] == expected


def test_discounts_come_only_from_a_campaign_running_that_day():
    campaigns = by_id("marketing_campaigns", "campaign_id")
    for order in rows("orders"):
        if order["campaign_id"] is None:
            assert order["discount_amount"] == 0
            continue
        campaign = campaigns[order["campaign_id"]]
        assert campaign["start_date"] <= order["order_date"] <= campaign["end_date"]
        assert order["discount_amount"] >= 0


def test_refunds_never_exceed_what_the_customer_paid():
    paid = defaultdict(Decimal)
    for item in rows("order_items"):
        paid[item["order_id"]] += item["unit_price"] * item["quantity"]
    for order in rows("orders"):
        paid[order["order_id"]] -= order["discount_amount"]
    refunded = defaultdict(Decimal)
    for refund in rows("refunds"):
        refunded[refund["order_id"]] += refund["amount"]
    assert all(0 < amount <= paid[order_id] for order_id, amount in refunded.items())


def test_only_delivered_orders_are_refunded_and_never_before_the_order():
    orders = by_id("orders", "order_id")
    for refund in rows("refunds"):
        order = orders[refund["order_id"]]
        assert order["status"] == "delivered"
        assert refund["refund_date"] > order["order_date"]


def test_subscription_status_matches_its_end_date():
    for subscription in rows("subscriptions"):
        if subscription["status"] == "active":
            assert subscription["ended_on"] is None
        else:
            assert subscription["status"] == "cancelled"
            assert subscription["ended_on"] > subscription["started_on"]


def test_the_shop_looks_like_a_real_one():
    orders = rows("orders")
    statuses = {order["status"] for order in orders}
    assert statuses == {"delivered", "shipped", "processing", "cancelled"}
    recent = [order for order in orders if (END - order["order_date"]).days <= 1]
    assert recent and all(order["status"] in ("processing", "cancelled") for order in recent)
    assert 0.02 < sum(order["status"] == "cancelled" for order in orders) / len(orders) < 0.06
    channels = {customer["acquisition_channel"] for customer in rows("customers")}
    assert None in channels and len(channels) > 4


def test_money_is_exact_to_the_cent():
    for name, table in TABLES.items():
        for index, (column, kind) in enumerate(table.columns):
            if kind.startswith("DECIMAL"):
                for row in table.rows:
                    value = row[index]
                    assert isinstance(value, Decimal) and value == value.quantize(Decimal("0.01")), (name, column)
