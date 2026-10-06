import json
from pathlib import Path

import pytest

from text_to_sql_agent.examples import Example
from text_to_sql_agent.llm import LLM
from text_to_sql_agent.schema import validate
from text_to_sql_agent.semantic import load_semantic
from text_to_sql_agent.writer import SCHEMA, build_messages, question_of, repair_messages, write_sql

LAYER = load_semantic(Path(__file__).resolve().parents[1] / "semantic.toml")
QUESTION = "Net revenue by platform in 2025?"
EXAMPLES = [Example("What's our net revenue for 2025?", "SELECT 1 AS checked_example")]


def messages():
    return build_messages(LAYER, LAYER.select_tables(QUESTION), EXAMPLES, QUESTION)


def test_the_schema_accepts_an_answer_and_a_decline():
    answer = {"sql": "SELECT 1", "explanation": "One.", "assumptions": [], "tables_used": []}
    decline = {"sql": None, "explanation": "That would change data.", "assumptions": [], "tables_used": []}
    assert validate(answer, SCHEMA) is answer
    assert validate(decline, SCHEMA) is decline


def test_the_schema_requires_all_four_fields():
    with pytest.raises(ValueError, match="tables_used: missing"):
        validate({"sql": "SELECT 1", "explanation": "x", "assumptions": []}, SCHEMA)


def test_the_prompt_has_the_rules_then_the_question_last_between_markers():
    system, user = messages()
    assert system["role"] == "system" and user["role"] == "user"
    assert "decline" in system["content"].lower()
    assert user["content"].rstrip().endswith(f"<<<\n{QUESTION}\n>>>")
    assert question_of(messages()) == QUESTION


def test_the_prompt_carries_the_tables_definitions_and_checked_examples():
    user = messages()[1]["content"]
    assert "Table orders:" in user and "Table refunds:" in user
    assert "Business definitions:" in user and "net revenue" in user
    assert "Today is 2026-09-30" in user
    assert "SELECT 1 AS checked_example" in user


def test_the_prompt_never_shows_the_restricted_table():
    everything = json.dumps(build_messages(LAYER, LAYER.allowed_tables, EXAMPLES, "Show customer emails"))
    assert "customer_pii" not in everything
    assert "street_address" not in everything


def test_a_repair_returns_the_failed_answer_and_the_error_to_the_model():
    previous = '{"sql": "SELECT nope FROM orders", "explanation": "x", "assumptions": [], "tables_used": []}'
    repaired = repair_messages(messages(), previous, "Binder Error: column nope not found")
    assert repaired[-2] == {"role": "assistant", "content": previous}
    assert "column nope not found" in repaired[-1]["content"]
    assert question_of(repaired) == QUESTION


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return {"content": self.answers.pop(0), "tool_calls": [], "prompt_tokens": 1800, "output_tokens": 120}


def test_write_sql_sends_the_schema_and_returns_the_validated_answer(tmp_path):
    answer = {"sql": "SELECT 1", "explanation": "One.", "assumptions": [], "tables_used": []}
    model = Scripted(json.dumps(answer))
    llm = LLM(mode="live", runs_root=tmp_path, num_ctx=16384, transport=model)
    reply = write_sql(llm, messages(), label="q-1")
    assert reply.data == answer
    assert model.requests[0]["format"] == SCHEMA
    assert model.requests[0]["options"]["num_ctx"] == 16384
