"""Command line.

python -m text_to_sql_agent demo              three questions from the stored run
python -m text_to_sql_agent accept            the acceptance test on the test split
python -m text_to_sql_agent accept --record   run it live on Ollama and store the run
python -m text_to_sql_agent ask "QUESTION"    ask one question live; nothing runs until you say yes
python -m text_to_sql_agent readme            refresh the results table in README.md
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .acceptance import (
    evaluate,
    load_criteria,
    report_dict,
    results_block,
    terminal_table,
    update_readme,
    write_reports,
)
from .agent import Agent, Answer, Proposal
from .database import connect
from .examples import ExampleBank
from .guardrails import MAX_ROWS
from .llm import LLM, ModelUnavailable, ReplayMiss, machine_description, ollama_info, run_dir_name
from .metrics import CATEGORIES, answer_correct, score
from .resultset import encode_cell
from .semantic import SemanticLayer, load_semantic

ROOT = Path(__file__).resolve().parents[2]
TITLE = "Text-to-SQL agent: acceptance test"
NUM_CTX = 8192  # the largest prompt plus answer measured 4,666 tokens
MODEL = "ministral-3:14b"  # on ministral-3:8b this agent missed the accuracy target: see the README
PREVIEW_ROWS = 10
LOGGED_ROWS = 50


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.data = self.root / "data"
        self.reports = self.root / "reports"
        self.readme = self.root / "README.md"
        self.acceptance = self.root / "acceptance.toml"
        self.semantic_file = self.root / "semantic.toml"
        self.warehouse = self.data / "warehouse.duckdb"

    def runs_root(self, split: str) -> Path:
        return self.root / "runs" if split == "test" else self.root / "runs" / split

    def out(self, split: str) -> Path:
        return self.root / "out" / split

    def labels(self, split: str) -> list[dict]:
        lines = (self.data / split / "labels.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def semantic(self) -> SemanticLayer:
        return load_semantic(self.semantic_file)

    def examples(self) -> ExampleBank:
        """Checked examples come from the practice split only."""
        return ExampleBank.from_labels(self.data / "practice" / "labels.jsonl")

    def database(self) -> Path:
        """The warehouse file, built by data/generate.py the first time it is needed (a few seconds)."""
        if not self.warehouse.exists():
            print("Building the made-up warehouse with data/generate.py (a few seconds)...", file=sys.stderr)
            spec = importlib.util.spec_from_file_location("warehouse_generator", self.data / "generate.py")
            generator = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = generator  # its dataclasses look their module up while being defined
            spec.loader.exec_module(generator)
            generator.build_database(self.warehouse)
        return self.warehouse


def _mode(args: argparse.Namespace) -> str:
    if getattr(args, "record", False):
        return "record"
    if getattr(args, "live", False):
        return "live"
    return os.environ.get("AGENT_MODE", "replay")


def _status(answer: Answer) -> str:
    if answer.outcome == "answered":
        text = f"answered, {len(answer.rows)} row{'' if len(answer.rows) == 1 else 's'}"
    else:
        text = answer.outcome
    if answer.repairs:
        text += f" after {answer.repairs} repair{'' if answer.repairs == 1 else 's'}"
    return text


def _run_split(ws: Workspace, split: str, agent: Agent, verbose: bool) -> tuple[list[dict], dict[str, Answer]]:
    labels, answers = ws.labels(split), {}
    for number, label in enumerate(labels, start=1):
        answer = agent.answer(label["question"], label=label["id"])
        answers[label["id"]] = answer
        if verbose:
            print(
                f"  [{number:>3}/{len(labels)}] {label['id']}  {_status(answer):<32} {answer.seconds:6.1f} s",
                flush=True,
            )
    return labels, answers


def _json_cell(value):
    try:
        return encode_cell(value)
    except TypeError:
        return str(value)


def _record(label: dict | None, answer: Answer) -> dict:
    record = {"id": label["id"], "kind": label["kind"], "category": label["category"]} if label else {}
    record.update(
        question=answer.question,
        outcome=answer.outcome,
        reason=answer.reason,
        approval=answer.approval,
        tables_shown=answer.tables_shown,
        sql=answer.sql,
        guarded_sql=answer.guarded_sql,
        explanation=answer.explanation,
        assumptions=answer.assumptions,
        tables_used=answer.tables_used,
        repairs=answer.repairs,
        errors=answer.errors,
        columns=answer.columns,
        row_count=len(answer.rows),
        rows=[[_json_cell(cell) for cell in row] for row in answer.rows[:LOGGED_ROWS]],
        executions=[asdict(execution) for execution in answer.executions],
        seconds=answer.seconds,
    )
    return record


def _write_outputs(ws: Workspace, split: str, labels: list[dict], answers: dict[str, Answer]) -> None:
    out = ws.out(split)
    out.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_record(label, answers[label["id"]]), ensure_ascii=False) for label in labels]
    (out / "answers.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _run_info_path(ws: Workspace, split: str, model: str) -> Path:
    return ws.runs_root(split) / run_dir_name(model) / "run.json"


def _median(seconds: list[float]) -> float | None:
    return round(statistics.median(seconds), 2) if seconds else None


def _write_run_info(ws: Workspace, split: str, llm: LLM, answers: dict[str, Answer], local_model: bool) -> None:
    info = {
        "recorded_at": date.today().isoformat(),
        "model": llm.model,
        **(ollama_info(llm.model) if local_model else {}),
        "machine": machine_description(),
        "split": split,
        "items": len(answers),
        "median_seconds_per_item": _median([answer.seconds for answer in answers.values()]),
        "item_seconds": {item: answer.seconds for item, answer in answers.items()},
    }
    path = _run_info_path(ws, split, llm.model)
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def cmd_accept(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    mode = _mode(args)
    llm = LLM(model=args.model, mode=mode, runs_root=ws.runs_root(args.split), num_ctx=NUM_CTX, transport=transport)
    if mode == "record":
        llm.start_recording()
    print(f"Acceptance test, {args.split} split, model {args.model}, mode {mode}")
    layer = ws.semantic()
    with connect(ws.database()) as connection:
        agent = Agent(layer, ws.examples(), llm, connection)
        try:
            labels, answers = _run_split(ws, args.split, agent, verbose=mode != "replay")
        except (ReplayMiss, ModelUnavailable) as error:
            print(f"\n{error}", file=sys.stderr)
            return 2
    _write_outputs(ws, args.split, labels, answers)
    if mode == "record":
        _write_run_info(ws, args.split, llm, answers, local_model=transport is None)
    info_path = _run_info_path(ws, args.split, args.model)
    info = json.loads(info_path.read_text(encoding="utf-8")) if mode != "live" and info_path.exists() else {}

    measures, details = score(labels, answers, allowed_tables=set(layer.allowed_tables))
    outcomes = evaluate(load_criteria(ws.acceptance), measures)
    median = info.get("median_seconds_per_item") or _median([answer.seconds for answer in answers.values()])
    meta = {
        "title": TITLE,
        "split": args.split,
        "items": len(labels),
        "model": args.model,
        "machine": info.get("machine") or machine_description(),
        "recorded_at": info.get("recorded_at"),
        "ollama_version": info.get("ollama_version"),
        "model_digest": info.get("model_digest"),
        "median_seconds_per_item": round(median, 1) if median is not None else None,
    }
    report = report_dict(outcomes, meta)
    folder = ws.reports if args.split == "test" else ws.reports / args.split
    write_reports(report, folder, details)
    print()
    print(terminal_table(outcomes))
    print(f"\n{'PASSED' if report['passed'] else 'FAILED'}: details in {folder}/acceptance.md")
    return 0 if report["passed"] else 1


def _show(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, datetime) and value.time() == datetime.min.time():
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def format_table(columns: list[str], rows: list[tuple], limit: int = PREVIEW_ROWS) -> list[str]:
    shown = [[_show(cell) for cell in row] for row in rows[:limit]]
    widths = [max([len(column), *(len(row[i]) for row in shown)]) for i, column in enumerate(columns)]
    lines = ["  ".join(column.ljust(width) for column, width in zip(columns, widths, strict=True)).rstrip()]
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in shown]
    more = f", first {limit} shown" if len(rows) > limit else ""
    lines.append(f"({len(rows)} row{'' if len(rows) == 1 else 's'}{more})")
    return lines


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def outcome_lines(answer: Answer) -> list[str]:
    if answer.outcome == "answered":
        return format_table(answer.columns, answer.rows)
    if answer.outcome == "cancelled":
        return ["Nothing was run."]
    if answer.outcome == "refused":
        return [f"Refused: {answer.reason}"]
    if answer.outcome == "declined":
        return [f"Declined: {answer.reason}"]
    return [f"Could not answer: {answer.reason}"]


def card(label: dict, answer: Answer) -> str:
    if label["kind"] == "question":
        kind = CATEGORIES.get(label["category"], label["category"])
    else:
        kind = f"unsafe request ({label['category']})"
    lines = [f"{label['id']}  {kind}", f"  Question: {label['question']}"]
    if answer.guarded_sql:
        lines += ["  SQL:", _indent(answer.guarded_sql)]
    elif answer.sql:
        lines += ["  The model wrote (never run):", _indent(answer.sql)]
    if answer.explanation and answer.outcome == "answered":
        lines.append(f"  Explanation: {answer.explanation}")
    lines += [f"  {line}" for line in outcome_lines(answer)]
    if label["kind"] == "question":
        verdict = "matches the reference answer" if answer_correct(label, answer) else "differs from the reference"
        repaired = f", after {answer.repairs} repair{'' if answer.repairs == 1 else 's'}" if answer.repairs else ""
        lines.append(f"  -> {verdict}{repaired}")
    else:
        lines.append("  -> blocked: nothing forbidden ran" if answer.refused else "  -> NOT blocked")
    return "\n".join(lines)


def cmd_demo(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    llm = LLM(
        model=args.model, mode=_mode(args), runs_root=ws.runs_root(args.split), num_ctx=NUM_CTX, transport=transport
    )
    with connect(ws.database()) as connection:
        agent = Agent(ws.semantic(), ws.examples(), llm, connection)
        try:
            labels, answers = _run_split(ws, args.split, agent, verbose=False)
        except (ReplayMiss, ModelUnavailable) as error:
            print(error, file=sys.stderr)
            return 2

    def correct(label: dict) -> bool:
        return label["kind"] == "question" and answer_correct(label, answers[label["id"]])

    def first(*tests) -> dict | None:
        return next((label for label in labels if all(test(label) for test in tests)), None)

    picks = [
        first(correct, lambda label: label["category"] == "definition") or first(correct),
        first(correct, lambda label: answers[label["id"]].repairs > 0)
        or first(correct, lambda label: label["category"] in ("growth", "top_n")),
        first(lambda label: label["kind"] == "unsafe", lambda label: answers[label["id"]].refused),
    ]
    shown: set[str] = set()
    for label in picks:
        if label is not None and label["id"] not in shown:
            shown.add(label["id"])
            print(card(label, answers[label["id"]]))
            print()
    return 0


def _confirm(proposal: Proposal) -> bool:
    print("SQL to run:")
    print(_indent(proposal.sql))
    if proposal.limit_added:
        print(f"  (the guardrails added the LIMIT line: at most {MAX_ROWS:,} rows come back)")
    print(f"Explanation: {proposal.explanation}")
    for assumption in proposal.assumptions:
        print(f"Assumption: {assumption}")
    try:
        reply = input("Run this query? [y/N] ")
    except EOFError:
        reply = ""
    return reply.strip().lower() in ("y", "yes")


def cmd_ask(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    llm = LLM(model=args.model, mode="live", runs_root=ws.runs_root("test"), num_ctx=NUM_CTX, transport=transport)
    with connect(ws.database()) as connection:
        agent = Agent(ws.semantic(), ws.examples(), llm, connection, approve=_confirm)
        try:
            answer = agent.answer(args.question, label="ask")
        except ModelUnavailable as error:
            print(error, file=sys.stderr)
            return 2
    print()
    print("\n".join(outcome_lines(answer)))
    out = ws.out("ask")
    out.mkdir(parents=True, exist_ok=True)
    entry = {"asked_at": datetime.now().isoformat(timespec="seconds"), **_record(None, answer)}
    with (out / "answers.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


def cmd_readme(args: argparse.Namespace, ws: Workspace, transport=None) -> int:
    report = json.loads((ws.reports / "acceptance.json").read_text(encoding="utf-8"))
    changed = update_readme(ws.readme, results_block(report), check=args.check)
    if args.check:
        print("README results are out of date: run `make readme`" if changed else "README results are up to date")
        return 1 if changed else 0
    print("README results updated" if changed else "README results already up to date")
    return 0


def main(argv: list[str] | None = None, *, root: Path = ROOT, transport=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m text_to_sql_agent", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    model = os.environ.get("AGENT_MODEL", MODEL)
    for name in ("demo", "accept"):
        command = commands.add_parser(name)
        command.add_argument("--live", action="store_true", help="ask the model instead of the stored run")
        command.add_argument("--record", action="store_true", help="ask the model and store the run")
        command.add_argument("--model", default=model)
        command.add_argument("--split", default="test", choices=["test", "practice"])
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--model", default=model)
    readme = commands.add_parser("readme")
    readme.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    handler = {"demo": cmd_demo, "accept": cmd_accept, "ask": cmd_ask, "readme": cmd_readme}[args.command]
    return handler(args, Workspace(root), transport)
