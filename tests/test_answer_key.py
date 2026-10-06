"""The answer key itself: complete, answerable inside the rules, and unambiguous.

These tests check the data, never the agent's answers: every reference query must run through the same
guardrails and checks as the model's SQL and give back its stored result, every unsafe request must
really be unsafe, and an ordered answer must have no ties, or its "correct" order would be arbitrary.
"""

import json
from collections import Counter
from pathlib import Path

import generate
import pytest
import sqlglot

from text_to_sql_agent.conventions import convention_problem
from text_to_sql_agent.database import check_plan, connect, run
from text_to_sql_agent.guardrails import MAX_ROWS, SecurityViolation, guard
from text_to_sql_agent.metrics import CATEGORIES, results_match
from text_to_sql_agent.resultset import decode_rows
from text_to_sql_agent.semantic import load_semantic

REPO = Path(__file__).resolve().parents[1]
LAYER = load_semantic(REPO / "semantic.toml")
SIZES = {"practice": (30, 5), "test": (50, 10)}
UNSAFE_KINDS = {"write", "pii", "dump", "injection", "file"}


def labels(split):
    lines = (REPO / "data" / split / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def checked(connection, sql):
    guarded = guard(
        sql,
        allowed_tables=LAYER.allowed_tables,
        restricted_tables=LAYER.restricted_tables,
        restricted_columns=LAYER.restricted_columns,
        today=LAYER.as_of,
    )
    check_plan(connection, guarded.sql, allowed_tables=LAYER.allowed_tables, restricted_tables=LAYER.restricted_tables)
    return run(connection, guarded.sql)


@pytest.mark.parametrize("split", SIZES)
def test_each_split_has_the_promised_mix(split):
    items = labels(split)
    questions = [item for item in items if item["kind"] == "question"]
    unsafe = [item for item in items if item["kind"] == "unsafe"]
    assert (len(questions), len(unsafe)) == SIZES[split]
    assert set(Counter(item["category"] for item in questions)) == set(CATEGORIES)
    assert {item["category"] for item in unsafe} == UNSAFE_KINDS
    assert len({item["id"] for item in items}) == len(items)


def test_no_question_appears_in_both_splits():
    practice = {item["question"].lower() for item in labels("practice")}
    assert not practice & {item["question"].lower() for item in labels("test")}


@pytest.mark.parametrize("split", SIZES)
def test_every_reference_query_runs_inside_the_rules_and_gives_its_stored_result(split, warehouse_db):
    with connect(warehouse_db) as connection:
        for item in labels(split):
            if item["kind"] != "question":
                continue
            result = checked(connection, item["sql"])
            assert 0 < result.row_count < MAX_ROWS, item["id"]
            reference = decode_rows(item["rows"])
            assert result.columns == item["columns"], item["id"]
            assert results_match(item["columns"], reference, result.columns, result.rows, ordered=True) or (
                not item["ordered"]
                and results_match(item["columns"], reference, result.columns, result.rows, ordered=False)
            ), item["id"]


@pytest.mark.parametrize("split", SIZES)
def test_every_reference_query_follows_the_conventions_the_agent_enforces(split):
    for item in labels(split):
        if item["kind"] == "question":
            assert convention_problem(item["question"], item["sql"], LAYER.as_of) is None, item["id"]


@pytest.mark.parametrize("split", SIZES)
def test_every_unsafe_request_is_really_unsafe(split, warehouse_db):
    with connect(warehouse_db) as connection:
        for item in labels(split):
            if item["kind"] != "unsafe":
                continue
            try:
                result = checked(connection, item["attack_sql"])
            except SecurityViolation:
                continue
            # The only attack that may pass the checks is an export, which the row cap catches.
            assert item["category"] == "dump" and result.row_count >= MAX_ROWS, item["id"]


@pytest.mark.parametrize("split", SIZES)
def test_ordered_answers_have_no_ties(split, warehouse_db):
    with connect(warehouse_db) as connection:
        for item in labels(split):
            if not item.get("ordered"):
                continue
            root = sqlglot.parse_one(item["sql"], read="duckdb")
            key = root.args["order"].expressions[0].this.name
            shown = len(item["rows"])
            one_more = connection.execute(root.limit(shown + 1).sql(dialect="duckdb")).fetchall()
            index = item["columns"].index(key)
            values = [row[index] for row in one_more]
            assert all(a != b for a, b in zip(values, values[1:], strict=False)), item["id"]


def test_the_committed_manifest_matches_the_generator(warehouse_db):
    committed = json.loads((REPO / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert generate.manifest_of(warehouse_db) == committed


@pytest.mark.parametrize("split", SIZES)
def test_the_committed_answer_key_matches_the_generator(split, warehouse_db):
    assert generate.build_labels(warehouse_db, split) == labels(split)
