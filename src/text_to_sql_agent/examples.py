"""Checked examples: past analyst questions with SQL that was verified by running it.

They come from the practice split only, so the test split stays unseen. For each new question the
most similar examples (BM25 over the question text) are shown to the model, the way a new analyst
learns from a colleague's past queries. An example is never shown for its own question.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .semantic import search_terms
from .textsearch import BM25

EXAMPLES_PER_QUESTION = 3


@dataclass(frozen=True)
class Example:
    question: str
    sql: str


class ExampleBank:
    def __init__(self, examples: list[Example], k: int = EXAMPLES_PER_QUESTION):
        self.examples = examples
        self.k = k
        self._index = BM25({str(number): search_terms(example.question) for number, example in enumerate(examples)})

    @classmethod
    def from_labels(cls, path: str | Path, k: int = EXAMPLES_PER_QUESTION) -> ExampleBank:
        """Read the answer key of the practice split; unsafe requests are left out."""
        examples = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                label = json.loads(line)
                if label["kind"] == "question":
                    examples.append(Example(question=label["question"], sql=label["sql"]))
        return cls(examples, k)

    def pick(self, question: str) -> list[Example]:
        hits = self._index.search(search_terms(question), k=self.k + 1)
        picked = [self.examples[int(number)] for number, _ in hits]
        return [example for example in picked if example.question.strip() != question.strip()][: self.k]
