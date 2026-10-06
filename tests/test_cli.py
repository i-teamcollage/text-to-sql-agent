import json
import shutil
from pathlib import Path

import pytest
from perfect_analyst import PerfectAnalyst

from text_to_sql_agent.cli import MODEL, main
from text_to_sql_agent.llm import run_dir_name

REPO = Path(__file__).resolve().parents[1]
README = "# Sample\n\n<!-- results:start -->\nNot measured yet.\n<!-- results:end -->\n"
PRACTICE_ITEMS = 35  # 30 questions and 5 unsafe requests


def workspace(tmp_path: Path, warehouse: Path | None) -> Path:
    shutil.copytree(REPO / "data", tmp_path / "data", ignore=shutil.ignore_patterns("*.duckdb*", "__pycache__"))
    for name in ("acceptance.toml", "semantic.toml"):
        shutil.copy(REPO / name, tmp_path / name)
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    if warehouse is not None:
        shutil.copy(warehouse, tmp_path / "data" / "warehouse.duckdb")
    return tmp_path


def record_practice(root: Path) -> PerfectAnalyst:
    analyst = PerfectAnalyst(root / "data", "practice")
    assert main(["accept", "--split", "practice", "--record"], root=root, transport=analyst) == 0
    return analyst


def report(root: Path, split: str = "practice") -> dict:
    folder = root / "reports" / split if split != "test" else root / "reports"
    return json.loads((folder / "acceptance.json").read_text(encoding="utf-8"))


def test_record_then_replay_gives_the_same_report_without_a_model(tmp_path, warehouse_db):
    root = workspace(tmp_path, warehouse_db)
    analyst = record_practice(root)
    recorded = report(root)
    assert analyst.calls == PRACTICE_ITEMS
    run_dir = root / "runs" / "practice" / run_dir_name(MODEL)
    assert (run_dir / "calls.jsonl").exists()
    run_info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_info["items"] == PRACTICE_ITEMS and run_info["split"] == "practice"

    assert main(["accept", "--split", "practice"], root=root) == 0
    replayed = report(root)
    assert replayed["criteria"] == recorded["criteria"]
    assert replayed["recorded_at"] == run_info["recorded_at"]
    assert replayed["median_seconds_per_item"] == recorded["median_seconds_per_item"]


def test_the_deterministic_layer_reaches_every_target_on_the_practice_split(tmp_path, warehouse_db):
    # With the reference SQL as the model's answer, every criterion is at 100%: nothing in the
    # guardrails, the limits, the execution or the scoring loses a correct answer, and every unsafe
    # request is blocked even when the model obeys it.
    root = workspace(tmp_path, warehouse_db)
    record_practice(root)
    values = {entry["id"]: (entry["hits"], entry["total"]) for entry in report(root)["criteria"]}
    assert values["execution_accuracy"] == (30, 30)
    assert values["unsafe_blocked"] == (5, 5)
    assert values["within_limits"] == (31, 31)  # 30 answers and the capped export
    assert report(root)["passed"]


def test_every_answer_is_logged_with_its_sql_and_its_approval(tmp_path, warehouse_db):
    root = workspace(tmp_path, warehouse_db)
    record_practice(root)
    lines = (root / "out" / "practice" / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    answers = {entry["id"]: entry for entry in map(json.loads, lines)}
    assert len(answers) == PRACTICE_ITEMS
    first = answers["p-01"]
    assert first["outcome"] == "answered"
    assert first["approval"] == "automatic (evaluation mode)"
    assert first["guarded_sql"].endswith("LIMIT 10000")
    assert first["explanation"]
    assert answers["p-31"]["outcome"] == "refused"
    assert answers["p-33"]["outcome"] == "refused" and "export" in answers["p-33"]["reason"]


def test_readme_block_follows_the_report_and_check_mode_detects_drift(tmp_path, warehouse_db):
    root = workspace(tmp_path, warehouse_db)
    record_practice(root)
    shutil.copy(root / "reports" / "practice" / "acceptance.json", root / "reports" / "acceptance.json")
    assert main(["readme", "--check"], root=root) == 1
    assert main(["readme"], root=root) == 0
    assert main(["readme", "--check"], root=root) == 0
    assert "Unsafe requests blocked" in (root / "README.md").read_text(encoding="utf-8")


def test_demo_replays_three_examples(tmp_path, warehouse_db, capsys):
    root = workspace(tmp_path, warehouse_db)
    record_practice(root)
    capsys.readouterr()
    assert main(["demo", "--split", "practice"], root=root) == 0
    shown = capsys.readouterr().out
    assert "SELECT" in shown
    assert "refused" in shown.lower()


def test_replay_without_a_stored_run_explains_what_to_do_and_builds_the_warehouse(tmp_path, capsys):
    root = workspace(tmp_path, warehouse=None)
    assert main(["accept"], root=root) == 2
    assert "make record" in capsys.readouterr().err
    assert (root / "data" / "warehouse.duckdb").exists()


def scripted(sql, explanation="Counts the orders placed in 2025."):
    content = json.dumps({"sql": sql, "explanation": explanation, "assumptions": [], "tables_used": ["orders"]})
    return lambda request: {"content": content, "tool_calls": [], "prompt_tokens": 2000, "output_tokens": 90}


ASK_SQL = "SELECT count(*) AS orders FROM orders WHERE order_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'"


def test_ask_shows_the_sql_and_runs_it_only_after_a_yes(tmp_path, warehouse_db, monkeypatch, capsys):
    root = workspace(tmp_path, warehouse_db)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "y")
    assert main(["ask", "How many orders in 2025?"], root=root, transport=scripted(ASK_SQL)) == 0
    shown = capsys.readouterr().out
    assert ASK_SQL in shown and "Counts the orders placed in 2025." in shown
    assert asked and "Run" in asked[0]
    assert "(1 row)" in shown
    log = (root / "out" / "ask" / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[-1])["approval"] == "analyst"


def test_ask_runs_nothing_after_a_no(tmp_path, warehouse_db, monkeypatch, capsys):
    root = workspace(tmp_path, warehouse_db)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert main(["ask", "How many orders in 2025?"], root=root, transport=scripted(ASK_SQL)) == 0
    assert "Nothing was run" in capsys.readouterr().out


def test_ask_explains_a_refusal(tmp_path, warehouse_db, monkeypatch, capsys):
    root = workspace(tmp_path, warehouse_db)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("a refused query must not be offered"))
    code = main(["ask", "Drop the refunds"], root=root, transport=scripted("DROP TABLE refunds", "Drops it."))
    assert code == 0
    assert "Refused" in capsys.readouterr().out
