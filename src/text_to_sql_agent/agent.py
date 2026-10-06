"""One question from text to answer: pick the tables, ask the model once, check, confirm, run.

1. The semantic layer picks the tables for the question; the definitions always come along, with a few
   checked examples from the practice questions.
2. One structured model call returns the SQL, an explanation, the assumptions and the tables used.
3. The guardrails check the SQL; DuckDB's own parser and plan check it again, with the cost check.
4. In interactive mode the analyst sees the SQL and the explanation and confirms. In evaluation mode
   the query is approved automatically, and the approval is logged with the answer.
5. The query runs on a read-only connection with a ten-second timeout.

A parse error, an execution error, a timeout or a runaway plan goes back to the model, at most twice.
A security violation is refused at once and never repaired. A result that reaches the 10,000-row cap
is an export of raw data, not an answer, so it is refused too. Each answer ends in one of five
outcomes: answered, refused (by the code), declined (by the model), failed, or cancelled (by the
analyst). Only "answered" shows a result; every other outcome comes with its reason.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import duckdb

from .database import TIMEOUT_SECONDS, DatabaseError, QueryTimeout, check_plan, run
from .examples import ExampleBank
from .guardrails import MAX_ROWS, SecurityViolation, SqlError, guard
from .llm import LLM, InvalidOutput
from .semantic import SemanticLayer
from .writer import build_messages, repair_messages, write_sql

MAX_REPAIRS = 2
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.DOTALL)
AUTO_APPROVAL = "automatic (evaluation mode)"
EXPORT_REFUSAL = (
    f"the result reached the {MAX_ROWS:,}-row cap: that is an export of raw data, not an answer, so it is "
    "not shown. Ask for a summary or a shorter list."
)


@dataclass(frozen=True)
class Proposal:
    """What the analyst sees before a query runs."""

    question: str
    sql: str
    explanation: str
    assumptions: list[str]
    limit_added: bool


@dataclass
class Execution:
    """One query that reached the database."""

    sql: str
    tables: list[str]
    rows: int
    seconds: float
    timed_out: bool = False
    error: str | None = None


@dataclass
class Answer:
    question: str
    outcome: str  # answered, refused, declined, failed or cancelled
    reason: str | None = None
    sql: str | None = None  # the model's SQL in the last attempt
    guarded_sql: str | None = None  # the SQL that ran: the model's SQL with the row limit applied
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)
    tables_used: list[str] = field(default_factory=list)  # as the model reported them
    tables_shown: list[str] = field(default_factory=list)  # the tables the model was given
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    repairs: int = 0
    errors: list[str] = field(default_factory=list)  # each error sent back to the model
    executions: list[Execution] = field(default_factory=list)
    model_seconds: float = 0.0
    approval: str | None = None

    @property
    def seconds(self) -> float:
        return round(self.model_seconds + sum(execution.seconds for execution in self.executions), 3)

    @property
    def refused(self) -> bool:
        """The agent said no: the code refused the query, or the model declined the request."""
        return self.outcome in ("refused", "declined")


class Agent:
    def __init__(
        self,
        layer: SemanticLayer,
        examples: ExampleBank,
        llm: LLM,
        connection: duckdb.DuckDBPyConnection,
        *,
        approve: Callable[[Proposal], bool] | None = None,
        timeout: float = TIMEOUT_SECONDS,
        max_repairs: int = MAX_REPAIRS,
    ):
        self.layer = layer
        self.examples = examples
        self.llm = llm
        self.connection = connection
        self.approve = approve  # None: evaluation mode, every query is approved automatically
        self.timeout = timeout
        self.max_repairs = max_repairs

    def answer(self, question: str, label: str = "") -> Answer:
        tables = self.layer.select_tables(question)
        messages = build_messages(self.layer, tables, self.examples.pick(question), question)
        answer = Answer(question=question, outcome="failed", tables_shown=tables)
        while True:
            try:
                reply = write_sql(self.llm, messages, label=_label(label, answer.repairs))
            except InvalidOutput as error:
                answer.reason = f"the model's answer could not be read ({error})"
                return answer
            answer.model_seconds = round(answer.model_seconds + reply.seconds, 3)
            data = reply.data
            answer.sql, answer.explanation = unfence(data["sql"]), data["explanation"]
            answer.assumptions, answer.tables_used = data["assumptions"], data["tables_used"]
            if not (answer.sql or "").strip():
                return _finish(answer, "declined", data["explanation"].strip() or "the model declined")
            try:
                problem = self._attempt(answer, question, answer.sql)
            except SecurityViolation as error:
                return _finish(answer, "refused", str(error))
            if problem is None:
                return answer
            answer.errors.append(problem)
            if answer.repairs == self.max_repairs:
                return _finish(answer, "failed", f"no working query after {self.max_repairs} repairs: {problem}")
            answer.repairs += 1
            messages = repair_messages(messages, reply.text, problem)

    def _attempt(self, answer: Answer, question: str, sql: str) -> str | None:
        """Check, confirm and run one query. Returns the error to send back, or None when finished.

        A SecurityViolation propagates: it is refused, never repaired.
        """
        try:
            guarded = guard(
                sql,
                allowed_tables=self.layer.allowed_tables,
                restricted_tables=self.layer.restricted_tables,
                restricted_columns=self.layer.restricted_columns,
                today=self.layer.as_of,
            )
            check_plan(
                self.connection,
                guarded.sql,
                allowed_tables=self.layer.allowed_tables,
                restricted_tables=self.layer.restricted_tables,
            )
        except (SqlError, DatabaseError) as error:
            return str(error)
        answer.guarded_sql = guarded.sql
        proposal = Proposal(question, guarded.sql, answer.explanation, answer.assumptions, guarded.limit_enforced)
        if self.approve is None:
            answer.approval = AUTO_APPROVAL
        elif self.approve(proposal):
            answer.approval = "analyst"
        else:
            _finish(answer, "cancelled", "the analyst chose not to run the query")
            return None
        started = time.perf_counter()
        try:
            result = run(self.connection, guarded.sql, timeout=self.timeout)
        except DatabaseError as error:
            seconds = round(time.perf_counter() - started, 3)
            timed_out = isinstance(error, QueryTimeout)
            answer.executions.append(Execution(guarded.sql, guarded.tables, 0, seconds, timed_out, str(error)))
            return str(error)
        answer.executions.append(Execution(guarded.sql, guarded.tables, result.row_count, result.seconds))
        if result.row_count >= MAX_ROWS:
            _finish(answer, "refused", EXPORT_REFUSAL)
            return None
        answer.columns, answer.rows = result.columns, result.rows
        _finish(answer, "answered", None)
        return None


def unfence(sql: str | None) -> str | None:
    """Take SQL out of a markdown code fence, which models sometimes add even inside JSON."""
    if sql is None:
        return None
    match = _FENCE.match(sql)
    return match.group(1).strip() if match else sql


def _finish(answer: Answer, outcome: str, reason: str | None) -> Answer:
    answer.outcome, answer.reason = outcome, reason
    return answer


def _label(label: str, repairs: int) -> str:
    return f"{label} (repair {repairs})" if repairs else label
