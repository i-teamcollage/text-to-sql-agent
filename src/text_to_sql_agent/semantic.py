"""The semantic layer: what the agent knows about the warehouse, and what it shows the model.

semantic.toml describes the tables the agent may use, how they join, the company's business definitions
and its conventions (what "last quarter" means, which scale a percentage uses). For each question:

1. a keyword search (BM25) over the table descriptions picks the most relevant tables;
2. a business term in the question (net revenue, AOV, churn) brings the tables its definition needs;
3. the tables needed to join everything are added, following the join paths.

The definitions and conventions are always included in full. The restricted table is listed only so the
guardrails know its name and its columns. It is never described, so the model never sees it.
"""

from __future__ import annotations

import re
import tomllib
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .textsearch import BM25, tokenize

TOP_TABLES = 3  # tables taken from the keyword search, before definitions and joins are added


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: dict[str, str]


@dataclass(frozen=True)
class Definition:
    name: str
    aliases: tuple[str, ...]
    description: str
    sql: str
    tables: tuple[str, ...]

    def mentioned_in(self, question: str) -> bool:
        text = question.lower()
        return any(re.search(rf"\b{re.escape(term.lower())}s?\b", text) for term in (self.name, *self.aliases))


@dataclass(frozen=True)
class Join:
    left: str  # "orders.customer_id"
    right: str  # "customers.customer_id"

    @property
    def tables(self) -> tuple[str, str]:
        return self.left.split(".")[0], self.right.split(".")[0]


class SemanticLayer:
    def __init__(
        self,
        *,
        as_of: date,
        allowed_tables: list[str],
        restricted_tables: list[str],
        restricted_columns: list[str],
        conventions: list[str],
        tables: dict[str, Table],
        joins: list[Join],
        definitions: list[Definition],
    ):
        self.as_of = as_of
        self.allowed_tables = allowed_tables
        self.restricted_tables = restricted_tables
        self.restricted_columns = restricted_columns
        self.conventions = conventions
        self.tables = tables
        self.joins = joins
        self.definitions = definitions
        self._index = BM25({name: _table_text(tables[name]) for name in allowed_tables})
        self._neighbours: dict[str, set[str]] = {name: set() for name in allowed_tables}
        for join in joins:
            a, b = join.tables
            self._neighbours[a].add(b)
            self._neighbours[b].add(a)

    def select_tables(self, question: str) -> list[str]:
        """The tables to show the model for this question, in the order of the allowed list."""
        picked = {name for name, _ in self._index.search(search_terms(question), k=TOP_TABLES)}
        for definition in self.definitions:
            if definition.mentioned_in(question):
                picked.update(definition.tables)
        if not picked:
            picked = {"orders"}
        return [name for name in self.allowed_tables if name in self._connect(picked)]

    def _connect(self, picked: set[str]) -> set[str]:
        """Add the tables on the join paths between the picked ones (the join graph is a tree)."""
        connected = set(picked)
        ordered = [name for name in self.allowed_tables if name in picked]
        for start, goal in zip(ordered, ordered[1:], strict=False):
            connected.update(self._path(start, goal))
        return connected

    def _path(self, start: str, goal: str) -> list[str]:
        previous: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node == goal:
                break
            for neighbour in sorted(self._neighbours[node]):
                if neighbour not in previous:
                    previous[neighbour] = node
                    queue.append(neighbour)
        path, node = [], goal
        while node is not None and node in previous:
            path.append(node)
            node = previous[node]
        return path

    def schema_prompt(self, tables: list[str]) -> str:
        """Describe the given tables, their columns and the joins between them."""
        blocks = []
        for name in tables:
            table = self.tables[name]
            lines = [f"Table {name}: {table.description}"]
            lines += [f"  - {column}: {text}" for column, text in table.columns.items()]
            blocks.append("\n".join(lines))
        joins = [join for join in self.joins if set(join.tables) <= set(tables)]
        if joins:
            blocks.append("Joins:\n" + "\n".join(f"  - {join.left} = {join.right}" for join in joins))
        return "\n\n".join(blocks)

    def definitions_prompt(self) -> str:
        """The conventions and every business definition, always shown in full."""
        lines = ["Conventions:"]
        lines += [f"- {convention}" for convention in self.conventions]
        lines += ["", "Business definitions:"]
        for definition in self.definitions:
            also = f" (also: {', '.join(definition.aliases)})" if definition.aliases else ""
            lines.append(f"- {definition.name}{also}: {definition.description}")
            sql = definition.sql.strip()
            if "\n" in sql:
                lines.append("  SQL:")
                lines += [f"    {line}" for line in sql.splitlines()]
            else:
                lines.append(f"  SQL: {sql}")
        return "\n".join(lines)


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def search_terms(text: str) -> str:
    """Words for the table search: singular forms, and no bare numbers (a year matches nothing useful)."""
    return " ".join(_stem(token) for token in tokenize(text.replace("_", " ")) if not token.isdigit())


def _table_text(table: Table) -> str:
    parts = [table.name, table.description]
    for column, text in table.columns.items():
        parts += [column, text]
    return search_terms(" ".join(parts))


def load_semantic(path: str | Path) -> SemanticLayer:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    tables = {
        name: Table(name=name, description=spec["description"], columns=dict(spec["columns"]))
        for name, spec in data["tables"].items()
    }
    restricted = set(data["restricted_tables"])
    if restricted & set(tables) or restricted & set(data["allowed_tables"]):
        raise ValueError("a restricted table must never be described or allowed")
    return SemanticLayer(
        as_of=data["as_of"],
        allowed_tables=list(data["allowed_tables"]),
        restricted_tables=list(data["restricted_tables"]),
        restricted_columns=list(data["restricted_columns"]),
        conventions=list(data["conventions"]),
        tables=tables,
        joins=[Join(item["left"], item["right"]) for item in data["joins"]],
        definitions=[
            Definition(
                name=item["name"],
                aliases=tuple(item.get("aliases", [])),
                description=item["description"],
                sql=item["sql"],
                tables=tuple(item["tables"]),
            )
            for item in data["definitions"]
        ],
    )
