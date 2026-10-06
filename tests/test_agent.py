import json
from pathlib import Path

import pytest

from text_to_sql_agent.agent import AUTO_APPROVAL, MAX_REPAIRS, Agent
from text_to_sql_agent.database import connect
from text_to_sql_agent.examples import Example, ExampleBank
from text_to_sql_agent.guardrails import MAX_ROWS
from text_to_sql_agent.llm import LLM
from text_to_sql_agent.semantic import load_semantic
from text_to_sql_agent.writer import SCHEMA

LAYER = load_semantic(Path(__file__).resolve().parents[1] / "semantic.toml")
BANK = ExampleBank([Example("How many orders were cancelled in 2025?", "SELECT 1")])
SLOW = "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 100000000) SELECT count(*) AS n FROM t"


def reply(sql, explanation="Counts the orders."):
    return json.dumps({"sql": sql, "explanation": explanation, "assumptions": [], "tables_used": ["orders"]})


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return {"content": self.answers.pop(0), "tool_calls": [], "prompt_tokens": 2100, "output_tokens": 140}


@pytest.fixture
def connection(warehouse_db):
    with connect(warehouse_db) as handle:
        yield handle


def agent_with(tmp_path, connection, *answers, **kwargs):
    model = Scripted(*answers)
    llm = LLM(mode="live", runs_root=tmp_path, num_ctx=16384, transport=model)
    return Agent(LAYER, BANK, llm, connection, **kwargs), model


def test_a_good_query_runs_and_its_rows_come_back(tmp_path, connection):
    agent, model = agent_with(tmp_path, connection, reply("SELECT count(*) AS orders FROM orders"))
    answer = agent.answer("How many orders have we had?", label="q-1")
    assert answer.outcome == "answered"
    assert answer.columns == ["orders"] and answer.rows[0][0] > 10_000
    assert answer.guarded_sql.endswith(f"\nLIMIT {MAX_ROWS}")
    assert answer.approval == AUTO_APPROVAL
    assert [execution.tables for execution in answer.executions] == [["orders"]]
    assert model.requests[0]["format"] == SCHEMA
    assert answer.seconds >= answer.executions[0].seconds


def test_the_model_sees_the_selected_tables_and_the_checked_examples(tmp_path, connection):
    agent, model = agent_with(tmp_path, connection, reply("SELECT 1 AS one"))
    answer = agent.answer("How many orders were cancelled last month?")
    prompt = model.requests[0]["messages"][1]["content"]
    assert "Table orders:" in prompt
    assert "Question: How many orders were cancelled in 2025?" in prompt
    assert "orders" in answer.tables_shown


def test_a_parse_error_is_sent_back_and_repaired(tmp_path, connection):
    agent, model = agent_with(
        tmp_path, connection, reply("SELECT count( FROM orders"), reply("SELECT count(*) AS n FROM orders")
    )
    answer = agent.answer("How many orders?")
    assert answer.outcome == "answered"
    assert answer.repairs == 1
    assert "could not be parsed" in model.requests[1]["messages"][-1]["content"]


def test_an_execution_error_is_sent_back_and_repaired(tmp_path, connection):
    agent, model = agent_with(
        tmp_path, connection, reply("SELECT count(no_such_column) FROM orders"), reply("SELECT count(*) FROM orders")
    )
    answer = agent.answer("How many orders?")
    assert answer.outcome == "answered" and answer.repairs == 1
    assert "no_such_column" in model.requests[1]["messages"][-1]["content"]


def test_a_made_up_table_is_a_mistake_to_repair(tmp_path, connection):
    agent, _ = agent_with(
        tmp_path, connection, reply("SELECT count(*) FROM sales"), reply("SELECT count(*) FROM orders")
    )
    assert agent.answer("How many sales?").outcome == "answered"


def test_there_are_at_most_two_repairs(tmp_path, connection):
    broken = reply("SELECT count( FROM orders")
    agent, model = agent_with(tmp_path, connection, broken, broken, broken, broken)
    answer = agent.answer("How many orders?")
    assert answer.outcome == "failed"
    assert answer.repairs == MAX_REPAIRS == 2
    assert len(model.requests) == 3
    assert not answer.executions


def test_a_security_violation_is_refused_at_once_and_never_repaired(tmp_path, connection):
    agent, model = agent_with(tmp_path, connection, reply("DELETE FROM orders"), reply("SELECT 1"))
    answer = agent.answer("Delete the old orders")
    assert answer.outcome == "refused"
    assert "DELETE" in answer.reason
    assert len(model.requests) == 1
    assert not answer.executions


def test_a_violation_after_a_repair_is_still_refused(tmp_path, connection):
    agent, model = agent_with(
        tmp_path, connection, reply("SELECT count( FROM orders"), reply("SELECT email FROM customer_pii")
    )
    answer = agent.answer("Customer emails please")
    assert answer.outcome == "refused" and answer.repairs == 1
    assert len(model.requests) == 2


def test_the_model_can_decline(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, reply(None, "Phone numbers are personal data."))
    answer = agent.answer("What is customer 12's phone number?")
    assert answer.outcome == "declined"
    assert answer.reason == "Phone numbers are personal data."
    assert answer.refused
    assert not answer.executions


def test_an_empty_query_counts_as_declining(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, reply("   ", "I cannot do that."))
    assert agent.answer("Drop everything").outcome == "declined"


def test_a_result_that_reaches_the_row_cap_is_refused_as_an_export(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, reply("SELECT * FROM order_items"))
    answer = agent.answer("Export every order line")
    assert answer.outcome == "refused"
    assert "export" in answer.reason
    assert answer.rows == []
    [execution] = answer.executions
    assert execution.rows == MAX_ROWS and execution.tables == ["order_items"]


def test_an_explicit_limit_at_the_cap_is_an_export_too(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, reply("SELECT * FROM customers LIMIT 10000"))
    assert agent.answer("Every customer, all columns").outcome == "refused"


def test_a_query_that_runs_too_long_is_stopped_and_sent_back(tmp_path, connection):
    agent, model = agent_with(tmp_path, connection, reply(SLOW), reply("SELECT 1 AS one"), timeout=0.3)
    answer = agent.answer("Count to a hundred million")
    assert answer.outcome == "answered" and answer.repairs == 1
    assert answer.executions[0].timed_out
    assert answer.executions[0].seconds < 5
    assert "seconds" in model.requests[1]["messages"][-1]["content"]


def test_a_cross_join_is_stopped_before_it_runs_and_sent_back(tmp_path, connection):
    agent, model = agent_with(
        tmp_path, connection, reply("SELECT count(*) FROM orders, order_items"), reply("SELECT count(*) FROM orders")
    )
    answer = agent.answer("Orders times lines")
    assert answer.outcome == "answered" and answer.repairs == 1
    assert len(answer.executions) == 1
    assert "cross join" in model.requests[1]["messages"][-1]["content"]


def test_an_unreadable_answer_fails_without_running_anything(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, "not json", "still not json")
    answer = agent.answer("How many orders?")
    assert answer.outcome == "failed"
    assert "could not be read" in answer.reason


def test_interactive_mode_shows_the_sql_and_explanation_and_runs_on_yes(tmp_path, connection):
    shown = []
    agent, _ = agent_with(
        tmp_path,
        connection,
        reply("SELECT count(*) AS n FROM orders", "Counts every order."),
        approve=lambda proposal: shown.append(proposal) or True,
    )
    answer = agent.answer("How many orders?")
    assert answer.outcome == "answered" and answer.approval == "analyst"
    assert shown[0].sql.endswith(f"LIMIT {MAX_ROWS}")
    assert shown[0].explanation == "Counts every order."


def test_interactive_mode_runs_nothing_on_no(tmp_path, connection):
    agent, _ = agent_with(tmp_path, connection, reply("SELECT count(*) FROM orders"), approve=lambda proposal: False)
    answer = agent.answer("How many orders?")
    assert answer.outcome == "cancelled"
    assert not answer.executions


def test_sql_wrapped_in_a_markdown_fence_is_unwrapped_not_repaired(tmp_path, connection):
    agent, model = agent_with(tmp_path, connection, reply("```sql\nSELECT count(*) AS n FROM orders\n```"))
    answer = agent.answer("How many orders?")
    assert answer.outcome == "answered" and answer.repairs == 0
    assert answer.guarded_sql.startswith("SELECT count(*) AS n FROM orders")


def test_a_query_that_reads_the_real_clock_is_sent_back(tmp_path, connection):
    agent, model = agent_with(
        tmp_path,
        connection,
        reply("SELECT count(*) FROM orders WHERE order_date >= current_date - 30"),
        reply("SELECT count(*) FROM orders WHERE order_date >= DATE '2026-09-01'"),
    )
    answer = agent.answer("How many orders in the last 30 days?")
    assert answer.outcome == "answered" and answer.repairs == 1
    assert "2026-09-30" in model.requests[1]["messages"][-1]["content"]
