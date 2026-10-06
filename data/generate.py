"""Generate the made-up retail warehouse and the answer key.

    python data/generate.py           build data/warehouse.duckdb, then write data/manifest.json and the labels
    python data/generate.py --db      build data/warehouse.duckdb only (make runs this when it is missing)
    python data/generate.py --check   rebuild in a temporary folder and compare with the committed files

Fixed seed, the repo's own word lists, no Faker: the warehouse never changes unless this file does.
Only integer and decimal arithmetic and the random module's basic draws are used, so the same rows
come out on every platform. Every customer, product, campaign and order is fictional. Personal data
uses the reserved example.com domains and a phone range set aside for fiction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import duckdb  # noqa: E402
from words import (  # noqa: E402
    ACQUISITION_CHANNELS,
    BLACK_FRIDAY_WEEKS,
    CAMPAIGNS,
    CATEGORIES,
    CITIES,
    EMAIL_DOMAINS,
    FIRST_NAMES,
    LAST_NAMES,
    ORDER_SEASON,
    PRICE_POINTS,
    PRODUCT_ITEMS,
    REFUND_REASONS,
    REGIONS,
    SEASON,
    STREETS,
)

from text_to_sql_agent.resultset import canonical_order, encode_rows  # noqa: E402

SEED = 2026
START = date(2024, 1, 1)  # the shop opened that day
END = date(2026, 9, 30)  # the last day loaded: "today" for every question
DB_PATH = HERE / "warehouse.duckdb"
MANIFEST = HERE / "manifest.json"
CENT = Decimal("0.01")
YEAR_PRICE = {2024: Decimal("0.94"), 2025: Decimal("0.97"), 2026: Decimal("1.00")}
SIGNUP_SEASON = [1.15, 1.0, 1.0, 1.05, 1.0, 0.9, 0.85, 0.85, 1.0, 1.05, 1.25, 1.2]
CATEGORY_WEIGHT = {
    "Cookware": 10,
    "Tableware": 11,
    "Kitchen Tools": 12,
    "Coffee and Tea": 9,
    "Bedding": 9,
    "Bath": 9,
    "Lighting": 6,
    "Decor": 9,
    "Garden Tools": 7,
    "Planters": 7,
    "Outdoor Furniture": 4,
    "Outdoor Cooking": 5,
}
# (name, weight for consumers, weight for businesses, days between orders, days before they stop ordering)
ARCHETYPES = [
    ("never", 13, 5, None, None),
    ("once", 32, 15, None, None),
    ("occasional", 30, 30, (120, 300), (200, 1000)),
    ("regular", 18, 30, (50, 130), (300, 1000)),
    ("frequent", 7, 20, (18, 60), (400, 1000)),
]
FIRST_ORDER_LAG = [0, 0, 0, 0, 0, 0, 1, 1, 2, 3, 5, 7, 10, 14, 21, 30, 45, 60]
ITEMS_PER_ORDER = ([1, 2, 3, 4, 5], [45, 28, 15, 8, 4])
QUANTITY = {"consumer": ([1, 2, 3, 4], [70, 20, 6, 4]), "business": ([1, 2, 3, 4, 6, 10], [40, 25, 15, 10, 5, 5])}
PAYMENT = {
    "consumer": [("card", 60), ("wallet", 30), ("bank transfer", 10)],
    "business": [("invoice", 60), ("card", 25), ("bank transfer", 15)],
}
APP_SHARE = {2024: 0.25, 2025: 0.35, 2026: 0.45}

TABLE_COLUMNS = {
    "regions": [
        ("region_id", "INTEGER"),
        ("region_name", "VARCHAR"),
        ("countries", "VARCHAR"),
        ("launched_on", "DATE"),
    ],
    "categories": [("category_id", "INTEGER"), ("category_name", "VARCHAR"), ("department", "VARCHAR")],
    "products": [
        ("product_id", "INTEGER"),
        ("product_name", "VARCHAR"),
        ("category_id", "INTEGER"),
        ("list_price", "DECIMAL(10,2)"),
        ("unit_cost", "DECIMAL(10,2)"),
        ("launched_on", "DATE"),
        ("discontinued_on", "DATE"),
    ],
    "customers": [
        ("customer_id", "INTEGER"),
        ("signup_date", "DATE"),
        ("country_code", "VARCHAR"),
        ("region_id", "INTEGER"),
        ("segment", "VARCHAR"),
        ("acquisition_channel", "VARCHAR"),
        ("marketing_opt_in", "BOOLEAN"),
    ],
    "customer_pii": [
        ("customer_id", "INTEGER"),
        ("first_name", "VARCHAR"),
        ("last_name", "VARCHAR"),
        ("email", "VARCHAR"),
        ("phone", "VARCHAR"),
        ("street_address", "VARCHAR"),
        ("postal_code", "VARCHAR"),
        ("city", "VARCHAR"),
    ],
    "marketing_campaigns": [
        ("campaign_id", "INTEGER"),
        ("campaign_name", "VARCHAR"),
        ("channel", "VARCHAR"),
        ("start_date", "DATE"),
        ("end_date", "DATE"),
        ("budget", "DECIMAL(10,2)"),
        ("discount_pct", "INTEGER"),
        ("department", "VARCHAR"),
    ],
    "orders": [
        ("order_id", "INTEGER"),
        ("customer_id", "INTEGER"),
        ("order_date", "DATE"),
        ("status", "VARCHAR"),
        ("platform", "VARCHAR"),
        ("payment_method", "VARCHAR"),
        ("campaign_id", "INTEGER"),
        ("discount_amount", "DECIMAL(10,2)"),
    ],
    "order_items": [
        ("order_item_id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("product_id", "INTEGER"),
        ("quantity", "INTEGER"),
        ("unit_price", "DECIMAL(10,2)"),
    ],
    "refunds": [
        ("refund_id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("refund_date", "DATE"),
        ("amount", "DECIMAL(10,2)"),
        ("reason", "VARCHAR"),
    ],
    "subscriptions": [
        ("subscription_id", "INTEGER"),
        ("customer_id", "INTEGER"),
        ("plan", "VARCHAR"),
        ("fee", "DECIMAL(10,2)"),
        ("started_on", "DATE"),
        ("ended_on", "DATE"),
        ("status", "VARCHAR"),
    ],
}


@dataclass
class Table:
    name: str
    columns: list[tuple[str, str]]
    rows: list[tuple]


@dataclass
class Product:
    product_id: int
    category: str
    department: str
    list_price: Decimal
    launched_on: date
    discontinued_on: date | None
    popularity: int

    def on_sale(self, day: date) -> bool:
        return self.launched_on <= day and (self.discontinued_on is None or day < self.discontinued_on)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _pick(rng: random.Random, pairs):
    values, weights = zip(*pairs, strict=True)
    return rng.choices(values, weights)[0]


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _build_products(rng: random.Random) -> tuple[list[tuple], list[Product]]:
    rows, products = [], []
    for category_id, name, department, (low, high), _ in CATEGORIES:
        items, variants = PRODUCT_ITEMS[name]
        combos = [f"{variant} {item}" for item in items for variant in variants]
        for product_name in sorted(rng.sample(combos, 20)):
            product_id = len(rows) + 1
            price = Decimal(PRICE_POINTS[rng.randrange(low, high + 1)])
            cost = _money(price * Decimal(rng.choice(["0.42", "0.48", "0.55", "0.61"])))
            launched = START if rng.random() < 0.75 else START + timedelta(days=rng.randrange(30, 940))
            discontinued = None
            if rng.random() < 0.10:
                earliest = launched + timedelta(days=120)
                if earliest < END - timedelta(days=60):
                    discontinued = earliest + timedelta(days=rng.randrange((END - earliest).days - 30))
            rows.append((product_id, product_name, category_id, price, cost, launched, discontinued))
            popularity = rng.choice([1, 2, 3, 5, 8, 13])
            products.append(Product(product_id, name, department, price, launched, discontinued, popularity))
    return rows, products


def _build_customers(rng: random.Random) -> list[tuple]:
    signups, growth, month = [], 1.0, START
    while month <= END:
        following = _next_month(month)
        days = (min(following, END + timedelta(days=1)) - month).days
        for _ in range(int(230 * growth * SIGNUP_SEASON[month.month - 1])):
            day = month + timedelta(days=rng.randrange(days))
            open_regions = [region for region in REGIONS if region[3] <= day]
            region = rng.choices(open_regions, [region[4] for region in open_regions])[0]
            country = _pick(rng, region[2])
            segment = "business" if rng.random() < 0.08 else "consumer"
            channel = _pick(rng, ACQUISITION_CHANNELS)
            signups.append((day, country, region[0], segment, channel, rng.random() < 0.55))
        growth *= 1.035
        month = following
    signups.sort(key=lambda signup: signup[0])
    return [(index + 1, *signup) for index, signup in enumerate(signups)]


def _build_pii(rng: random.Random, customers: list[tuple]) -> list[tuple]:
    rows = []
    for customer_id, _, country, *_ in customers:
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        email = f"{first}.{last}.{customer_id}@{rng.choice(EMAIL_DOMAINS)}".lower()
        phone = f"+44 7700 900{rng.randrange(1000):03d}"
        street = f"{rng.randrange(1, 180)} {rng.choice(STREETS)}"
        rows.append((customer_id, first, last, email, phone, street, f"{rng.randrange(10000, 99999)}",
                     rng.choice(CITIES[country])))  # fmt: skip
    return rows


def _order_days(rng: random.Random, customer: tuple) -> list[date]:
    _, signup, _, _, segment, *_ = customer
    weights = [row[1] if segment == "consumer" else row[2] for row in ARCHETYPES]
    name, _, _, gap, lifetime = rng.choices(ARCHETYPES, weights)[0]
    if name == "never":
        return []
    first = signup + timedelta(days=rng.choice(FIRST_ORDER_LAG))
    if first > END:
        return []
    days = [first]
    if gap:
        stop = min(END, first + timedelta(days=rng.randrange(*lifetime)))
        day = first
        while True:
            day += timedelta(days=rng.randrange(gap[0], gap[1] + 1))
            if day > stop:
                break
            if rng.random() < ORDER_SEASON[day.month - 1]:
                days.append(day)
    for week_start, week_end in BLACK_FRIDAY_WEEKS:
        if signup <= week_start and rng.random() < 0.10:
            days.append(week_start + timedelta(days=rng.randrange((week_end - week_start).days + 1)))
    for year in (2024, 2025):
        if signup <= date(year, 12, 1) and rng.random() < 0.05:
            days.append(date(year, 12, 1) + timedelta(days=rng.randrange(20)))
    return sorted(days)


class Catalogue:
    def __init__(self, products: list[Product]):
        self.by_category: dict[str, list[Product]] = {}
        for product in products:
            self.by_category.setdefault(product.category, []).append(product)
        self.categories = list(self.by_category)
        self.month_weights = [
            [CATEGORY_WEIGHT[name] * SEASON.get(name, [1.0] * 12)[month] for name in self.categories]
            for month in range(12)
        ]

    def pick(self, rng: random.Random, day: date) -> Product | None:
        category = rng.choices(self.categories, self.month_weights[day.month - 1])[0]
        candidates = [product for product in self.by_category[category] if product.on_sale(day)]
        if not candidates:
            return None
        return rng.choices(candidates, [product.popularity for product in candidates])[0]


def _attribution(rng: random.Random, campaigns: list[tuple], day: date, lines: list) -> tuple[int | None, Decimal]:
    running = [campaign for campaign in campaigns if campaign[3] <= day <= campaign[4]]
    if not running:
        return None, Decimal("0.00")
    campaign_id, _, _, _, _, _, discount_pct, department = rng.choice(running)
    eligible = [line for line in lines if department is None or line[0].department == department]
    if not eligible or rng.random() >= 0.35:
        return None, Decimal("0.00")
    base = sum((line[2] * line[1] for line in eligible), Decimal("0"))
    return campaign_id, _money(base * discount_pct / 100)


def _refunds(rng: random.Random, day: date, lines: list, paid: Decimal) -> list[tuple[date, Decimal, str]]:
    chance = max(propensity for line in lines for _, name, _, _, propensity in CATEGORIES if name == line[0].category)
    chance = chance / 1000 + (0.02 if day.month == 12 else 0.0)
    if rng.random() >= chance:
        return []
    refund_day = day + timedelta(days=rng.randrange(3, 46))
    if refund_day > END:
        return []
    reasons = REFUND_REASONS if day.month != 12 else [*REFUND_REASONS, "late delivery", "late delivery"]
    single = len(lines) == 1 and lines[0][1] == 1
    if single or rng.random() < 0.4:
        return [(refund_day, paid, rng.choice(reasons))]
    first = rng.choice(lines)
    amount = min(first[2], paid)
    refunds = [(refund_day, amount, rng.choice(reasons))]
    others = [line for line in lines if line is not first]
    if others and rng.random() < 0.10:
        second = min(rng.choice(others)[2], paid - amount)
        if second > 0:
            refunds.append((refund_day + timedelta(days=rng.randrange(1, 8)), second, rng.choice(reasons)))
    return [refund for refund in refunds if refund[0] <= END]


def _build_orders(rng: random.Random, customers: list[tuple], catalogue: Catalogue, campaigns: list[tuple]):
    planned = []
    for customer in customers:
        for sequence, day in enumerate(_order_days(rng, customer)):
            planned.append((day, customer[0], sequence, customer))
    planned.sort(key=lambda plan: plan[:3])
    orders, items, refunds = [], [], []
    for order_id, (day, customer_id, _, customer) in enumerate(planned, start=1):
        segment = customer[4]
        count = rng.choices(*ITEMS_PER_ORDER)[0] + (1 if segment == "business" and rng.random() < 0.5 else 0)
        lines, used = [], set()
        for _ in range(count):
            product = catalogue.pick(rng, day)
            if product is None or product.product_id in used:
                continue
            used.add(product.product_id)
            lines.append(
                (product, rng.choices(*QUANTITY[segment])[0], _money(product.list_price * YEAR_PRICE[day.year]))
            )
        if not lines:
            product = next(p for p in catalogue.by_category["Kitchen Tools"] if p.on_sale(day))
            lines.append((product, 1, _money(product.list_price * YEAR_PRICE[day.year])))
        campaign_id, discount = _attribution(rng, campaigns, day, lines)
        days_ago = (END - day).days
        if rng.random() < (0.02 if segment == "business" else 0.04):
            status = "cancelled"
        elif days_ago <= 1:
            status = "processing"
        elif days_ago <= 5:
            status = "shipped"
        else:
            status = "delivered"
        platform = "app" if rng.random() < APP_SHARE[day.year] else "web"
        payment = _pick(rng, PAYMENT[segment])
        orders.append((order_id, customer_id, day, status, platform, payment, campaign_id, discount))
        for product, quantity, unit_price in lines:
            items.append((len(items) + 1, order_id, product.product_id, quantity, unit_price))
        if status == "delivered":
            paid = sum((line[2] * line[1] for line in lines), Decimal("0")) - discount
            for refund_day, amount, reason in _refunds(rng, day, lines, paid):
                refunds.append((refund_day, order_id, amount, reason))
    refunds.sort(key=lambda refund: refund[:2])
    refund_rows = [
        (index + 1, order_id, day, amount, reason) for index, (day, order_id, amount, reason) in enumerate(refunds)
    ]
    return orders, items, refund_rows


def _subscription_chain(rng: random.Random, customer_id: int, segment: str, start: date) -> list[tuple]:
    chain = []
    while start <= END:
        annual = rng.random() < 0.30
        ended = None
        if annual:
            year = 1
            while (renewal := date(start.year + year, start.month, min(start.day, 28))) <= END:
                if rng.random() < 0.25:
                    ended = renewal
                    break
                year += 1
        else:
            month = 1
            while (renewal := start + timedelta(days=30 * month)) <= END:
                if rng.random() < (0.03 if segment == "business" else 0.05):
                    ended = renewal
                    break
                month += 1
        plan, fee = ("annual", Decimal("49.00")) if annual else ("monthly", Decimal("4.99"))
        chain.append((start, customer_id, plan, fee, ended, "cancelled" if ended else "active"))
        if ended is None or rng.random() >= 0.15:
            break
        start = ended + timedelta(days=rng.randrange(30, 201))
    return chain


def _build_subscriptions(rng: random.Random, customers: list[tuple], orders: list[tuple]) -> list[tuple]:
    first_order: dict[int, date] = {}
    for _, customer_id, day, *_ in orders:
        first_order.setdefault(customer_id, day)
    segments = {customer[0]: customer[4] for customer in customers}
    chains = []
    for customer_id, first in sorted(first_order.items()):
        if rng.random() < 0.22:
            chains += _subscription_chain(
                rng, customer_id, segments[customer_id], first + timedelta(rng.randrange(121))
            )
    chains.sort(key=lambda row: row[:2])
    return [
        (index + 1, customer_id, plan, fee, start, ended, status)
        for index, (start, customer_id, plan, fee, ended, status) in enumerate(chains)
    ]


def build_tables(seed: int = SEED) -> dict[str, Table]:
    rng = random.Random(seed)
    regions = [
        (rid, name, ", ".join(sorted(c for c, _ in countries)), launched)
        for rid, name, countries, launched, _ in REGIONS
    ]
    categories = [(category_id, name, department) for category_id, name, department, _, _ in CATEGORIES]
    product_rows, products = _build_products(rng)
    campaign_list = sorted(CAMPAIGNS, key=lambda campaign: (campaign[2], campaign[0]))
    campaigns = [
        (index + 1, name, channel, start, end, Decimal(budget), discount, department)
        for index, (name, channel, start, end, budget, discount, department) in enumerate(campaign_list)
    ]
    customers = _build_customers(rng)
    pii = _build_pii(rng, customers)
    orders, items, refunds = _build_orders(rng, customers, Catalogue(products), campaigns)
    subscriptions = _build_subscriptions(rng, customers, orders)
    rows = {
        "regions": regions,
        "categories": categories,
        "products": product_rows,
        "customers": customers,
        "customer_pii": pii,
        "marketing_campaigns": campaigns,
        "orders": orders,
        "order_items": items,
        "refunds": refunds,
        "subscriptions": subscriptions,
    }
    return {name: Table(name, TABLE_COLUMNS[name], rows[name]) for name in TABLE_COLUMNS}


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value.isoformat() if isinstance(value, date) else str(value)


def build_database(path: str | Path, tables: dict[str, Table] | None = None) -> dict:
    """Write the warehouse to a DuckDB file and return its manifest."""
    tables = tables or build_tables()
    path = Path(path)
    for stale in (path, path.with_name(path.name + ".wal")):
        stale.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        connection = duckdb.connect(str(path))
        for table in tables.values():
            columns = ", ".join(f"{column} {kind}" for column, kind in table.columns)
            connection.execute(f"CREATE TABLE {table.name} ({columns}, PRIMARY KEY ({table.columns[0][0]}))")
            source = Path(folder) / f"{table.name}.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle, lineterminator="\n").writerows([_csv_value(v) for v in row] for row in table.rows)
            quoted = str(source).replace("'", "''")
            connection.execute(f"COPY {table.name} FROM '{quoted}' (FORMAT csv, HEADER false, NULLSTR '')")
        connection.execute("CHECKPOINT")
        connection.close()
    return manifest_of(path)


def _cell(value) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "true" if value else "false"
    return value.isoformat() if isinstance(value, date) else str(value)


def manifest_of(path: str | Path) -> dict:
    """Row count and SHA-256 of every table, read back from the database in primary-key order."""
    connection = duckdb.connect(str(path), read_only=True)
    try:
        tables = {}
        for name, columns in sorted(TABLE_COLUMNS.items()):
            digest, count = hashlib.sha256(), 0
            cursor = connection.execute(f"SELECT * FROM {name} ORDER BY {columns[0][0]}")
            while batch := cursor.fetchmany(10_000):
                for row in batch:
                    digest.update(("\t".join(_cell(value) for value in row) + "\n").encode("utf-8"))
                    count += 1
            tables[name] = {"rows": count, "sha256": digest.hexdigest()}
    finally:
        connection.close()
    return {"as_of": END.isoformat(), "tables": tables}


def _manifest_text(manifest: dict) -> str:
    return json.dumps(manifest, indent=2) + "\n"


def build_labels(path: str | Path, split: str) -> list[dict]:
    """Run every reference query of a split against the warehouse and store its result (the answer key).

    This is how the reference SQL is verified: if a query is wrong, breaks, or the data shifts under it,
    the stored result changes and `make data-check` fails. Unsafe requests carry no result.
    """
    spec = tomllib.loads((HERE / split / "questions.toml").read_text(encoding="utf-8"))
    connection = duckdb.connect(str(path), read_only=True)
    labels: list[dict] = []
    try:
        for question in spec["questions"]:
            cursor = connection.execute(question["sql"])
            columns = [description[0] for description in cursor.description]
            rows = encode_rows(cursor.fetchall())
            ordered = bool(question.get("ordered", False))
            labels.append(
                {
                    "id": question["id"],
                    "kind": "question",
                    "category": question["category"],
                    "question": question["question"],
                    "sql": question["sql"].strip(),
                    "ordered": ordered,
                    "columns": columns,
                    "rows": rows if ordered else canonical_order(rows),
                }
            )
    finally:
        connection.close()
    for request in spec.get("unsafe", []):
        labels.append(
            {
                "id": request["id"],
                "kind": "unsafe",
                "category": request["category"],
                "question": request["question"],
                "attack_sql": request["attack_sql"].strip(),
            }
        )
    return labels


def _labels_text(labels: list[dict]) -> str:
    return "".join(json.dumps(label, ensure_ascii=False) + "\n" for label in labels)


def all_files(db_path: Path) -> dict[str, str]:
    """Build the database at db_path and return the committed files it implies: the manifest and the
    answer key for both splits."""
    manifest = build_database(db_path)
    files = {"manifest.json": _manifest_text(manifest)}
    for split in ("practice", "test"):
        files[f"{split}/labels.jsonl"] = _labels_text(build_labels(db_path, split))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="rebuild in a temporary folder and compare")
    mode.add_argument("--db", action="store_true", help="build data/warehouse.duckdb only")
    args = parser.parse_args()
    if args.db:
        manifest = build_database(DB_PATH)
        committed = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
        note = "" if _manifest_text(manifest) == committed else " (it differs from data/manifest.json: run `make data`)"
        print(f"built data/{DB_PATH.name}{note}")
        return 0
    if args.check:
        with tempfile.TemporaryDirectory() as folder:
            files = all_files(Path(folder) / "warehouse.duckdb")
        stale = [
            path
            for path, content in files.items()
            if not (HERE / path).exists() or (HERE / path).read_text(encoding="utf-8") != content
        ]
        for path in stale:
            print(f"differs: data/{path}")
        print("data is up to date" if not stale else f"{len(stale)} file(s) differ: run `make data`")
        return 1 if stale else 0
    files = all_files(DB_PATH)
    for path, content in files.items():
        target = HERE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"built data/{DB_PATH.name} and wrote {len(files)} files under data/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
