from pathlib import Path

from text_to_sql_agent.examples import Example, ExampleBank

PRACTICE = Path(__file__).resolve().parents[1] / "data" / "practice" / "labels.jsonl"
BANK = ExampleBank.from_labels(PRACTICE)


def test_the_bank_holds_the_practice_questions_only_never_the_unsafe_requests():
    assert len(BANK.examples) == 30
    assert all(example.sql.lstrip().upper().startswith(("SELECT", "WITH")) for example in BANK.examples)
    assert not any("customer_pii" in example.sql for example in BANK.examples)


def test_the_most_similar_question_comes_first():
    picked = BANK.pick("What was net revenue in the first quarter of 2026?")
    assert "net revenue" in picked[0].question.lower()


def test_an_example_is_never_shown_for_its_own_question():
    question = "What's our net revenue for 2025?"
    assert question in [example.question for example in BANK.examples]
    assert question not in [example.question for example in BANK.pick(question)]


def test_pick_returns_at_most_k_examples_and_never_shuffles():
    bank = ExampleBank(
        [
            Example("How many orders in May?", "SELECT 1"),
            Example("How many refunds in May?", "SELECT 2"),
            Example("Revenue by region", "SELECT 3"),
        ],
        k=2,
    )
    first = bank.pick("orders and refunds in May")
    assert len(first) == 2
    assert first == bank.pick("orders and refunds in May")


def test_a_question_with_no_common_words_gets_no_examples():
    assert BANK.pick("zebra quokka") == []
