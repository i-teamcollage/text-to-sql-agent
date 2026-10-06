"""A stand-in model for tests: it answers every question with the reference SQL from the answer key.

For an unsafe request it does what a careless model would do: it obeys and writes the attack SQL. So
it measures what the deterministic layer allows (guardrails, limits, execution and scoring), not what
a model can write. Only the practice split is used with it.
"""

import json
from pathlib import Path

from text_to_sql_agent.writer import question_of


class PerfectAnalyst:
    def __init__(self, data_dir: Path, split: str):
        self.by_question = {}
        for line in (data_dir / split / "labels.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                label = json.loads(line)
                self.by_question[label["question"]] = label
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        label = self.by_question[question_of(request["messages"])]
        sql = label["sql"] if label["kind"] == "question" else label["attack_sql"]
        answer = {
            "sql": sql,
            "explanation": f"The query answers: {label['question']}",
            "assumptions": [],
            "tables_used": [],
        }
        return {"content": json.dumps(answer), "tool_calls": [], "prompt_tokens": 2400, "output_tokens": 160}
