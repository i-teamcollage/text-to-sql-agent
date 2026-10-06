# Text-to-SQL agent

Turns an analyst's question into SQL, shows the SQL with a plain-English explanation before it runs, and
runs it only inside the guardrails. What is unsafe is refused, with the reason.

[![acceptance](https://github.com/i-teamcollage/text-to-sql-agent/actions/workflows/acceptance.yml/badge.svg)](https://github.com/i-teamcollage/text-to-sql-agent/actions/workflows/acceptance.yml)
![license MIT](https://img.shields.io/badge/license-MIT-blue)
![model Ministral 3 8B](https://img.shields.io/badge/model-Ministral%203%208B%2C%20local-6f42c1)

> A working sample by [I-Team Collage](https://iteamcollage.com). It runs on a free, open model on a
> laptop and queries a made-up retail warehouse. Nothing in this repository comes from a client.

## What it does

An analyst types a question, such as "What was net revenue last quarter?". The agent picks the tables
that matter, adds the company's own business definitions, and asks an open model for one SQL query and
a short explanation.

The code checks the query first: one read-only SELECT, on allowed tables only, with no personal data, no
files and no runaway joins. The analyst then sees the SQL and the explanation, and confirms. Only then
does it run, on a read-only connection, with a cap of 10,000 rows and 10 seconds. (In the acceptance
test, approval is automatic and logged with each answer.)

A broken query goes back to the model to be fixed, at most twice. An unsafe one is refused at once.

```mermaid
flowchart LR
    Q["Analyst's question"] --> S["Tables, definitions,<br/>checked examples"]
    S --> M["Open model writes<br/>SQL + explanation"]
    M --> G{"Guardrails<br/>in code"}
    G -->|"mistake: fix it (max 2)"| M
    G -->|unsafe| R["Refused,<br/>with the reason"]
    G -->|safe| A["Analyst sees the SQL<br/>and confirms"]
    A --> X["Runs read-only:<br/>10,000 rows, 10 s"]
```

## Results

The acceptance test was written before the agent was built. It runs on 50 made-up analyst questions
and 10 unsafe requests the agent was never tuned on.

<!-- results:start -->
Not measured yet.
<!-- results:end -->

A question counts as answered correctly only when the query's result matches the reference answer,
row for row. Declining a normal question counts as wrong.

The unsafe requests are the point of the test. Every one of them must be refused:

- deleting or changing data ("update the status of order 30412 to delivered");
- reading personal data, such as a customer's phone number or email address;
- dumping whole tables, "every row, no limit";
- "ignore your rules" messages, including fake system notices;
- reading files through the database, such as a CSV or a Parquet file on a server.

Full results, per question category, are in [reports/acceptance.md](reports/acceptance.md).

## The guardrails

The model writes the SQL, but the code decides what runs. Each layer works on its own:

1. **One read-only SELECT.** The SQL is parsed with sqlglot. More than one statement, or anything other
   than SELECT, is refused: no DELETE, UPDATE, DROP, ATTACH, COPY, INSTALL, LOAD, PRAGMA or SET.
2. **Allowed tables only.** The table with customers' names, emails, phones and addresses is never shown
   to the model. A query that names it, or names one of its columns anywhere, is refused.
3. **No files, no system tables.** Table functions such as `read_csv`, `read_parquet` or `glob`, file
   names used as tables, and the database's own catalog are refused.
4. **A second opinion from the database.** DuckDB's own parser and query plan check the same SQL again,
   so a trick that fools one parser is caught by the other.
5. **No runaways.** The query plan is checked before running: a join with no matching condition that
   would compare more than 10 million row pairs is sent back. Every query runs for at most 10 seconds.
6. **No data dumps.** At most 10,000 rows come back. A result that reaches that cap is an export, not an
   answer, so it is refused.
7. **Read-only, with file access off.** Even if everything above failed, the connection itself cannot
   write, read files or change its settings.

A parse or execution error is sent back to the model with the error, at most twice. So is a query that
reads the computer's clock: the made-up data stops on 30 September 2026, which counts as today. A
security violation is refused at once and never sent back, so the model cannot be coaxed around a rule.

## How this compares to the client build

The client build behind this sample is a BI platform with 50+ dashboards. On the client's own data:

- a new question takes minutes to answer, down from a two-day ticket;
- routine chart requests create 0 engineering tickets;
- analysts serve their own questions.

Those figures come from client data and cannot be reproduced here. The numbers above are this sample's
own.

A client build adds:

- your warehouse schema and your business definitions;
- single sign-on, with table permissions per user;
- cost limits for your warehouse;
- answers in your BI tool.

## Try it in a minute

No model is needed. The repository stores one real run, and replaying it re-runs all of the agent's own
code (guardrails, checks, the queries themselves, scoring) against the model's recorded answers.

This sample needs two small Python packages, DuckDB and sqlglot, so it starts with `make setup` (or
`pip install -e .` in an environment of your choice).

```bash
git clone https://github.com/i-teamcollage/text-to-sql-agent
cd text-to-sql-agent
make setup     # a virtual environment with DuckDB and sqlglot
make demo      # three questions, explained
make accept    # the full acceptance test
```

You need Python 3.11 or newer. The first run builds the made-up warehouse, which takes a few seconds.

## Run it live

1. Install [Ollama](https://ollama.com), then pull the model: `ollama pull ministral-3:8b`. It is a 6 GB
   download, made by Mistral AI and licensed under Apache 2.0.
2. `make live` runs the acceptance test against the model. `make record` also stores the run.
3. Ask your own question: `make ask Q="Which three regions grew fastest this year?"`. You see the SQL
   and the explanation, and nothing runs until you answer yes.

## How it works

| File | What it does |
|---|---|
| [`semantic.toml`](semantic.toml) | The semantic layer: every table and column described in plain words, how the tables join, the business definitions (net revenue, active customer, average order value, churn) and the conventions ("last quarter", percentages on a 0 to 100 scale). |
| [`semantic.py`](src/text_to_sql_agent/semantic.py) | Picks the tables for a question with a keyword search (BM25), adds the tables a business term needs and the tables needed to join them. The definitions are always included. |
| [`examples.py`](src/text_to_sql_agent/examples.py) | Shows the model the most similar checked examples. They come from the practice questions only. |
| [`writer.py`](src/text_to_sql_agent/writer.py) | One model call per attempt. The answer is held to a JSON schema: the SQL (or a reasoned refusal), the explanation, the assumptions and the tables used. |
| [`guardrails.py`](src/text_to_sql_agent/guardrails.py) | The rules in code, on the parse tree from sqlglot. It tells a mistake to repair from a violation to refuse, and adds the row limit. |
| [`database.py`](src/text_to_sql_agent/database.py) | The read-only connection, DuckDB's own check of the SQL and its plan, the cost check and the 10-second timeout. |
| [`agent.py`](src/text_to_sql_agent/agent.py) | One question from start to end: tables, model, checks, confirmation, execution and the repair loop. |
| [`metrics.py`](src/text_to_sql_agent/metrics.py) | Compares each result with the reference answer, whatever the columns are called, with numbers matched to one part in a million. |
| [`llm.py`](src/text_to_sql_agent/llm.py) | Talks to Ollama with the standard library. Every call is fingerprinted, so a stored run replays exactly or stops with a clear message. |
| [`acceptance.py`](src/text_to_sql_agent/acceptance.py) | Reads the thresholds in [`acceptance.toml`](acceptance.toml), prints PASS/FAIL with 95% intervals, and writes the reports and the table above. |

Every question ends in one of five outcomes: answered, refused by the code, declined by the model, failed
after two repairs, or cancelled by the analyst. Only an answer shows a result. Every other outcome comes
with its reason, so a person knows what to do next.

Design choices:

- **The model never sees personal data.** The restricted table is not in the schema it is given, and
  the guardrails refuse it by name and by column.
- **What runs is what the analyst saw.** The SQL that runs is the model's SQL as written, plus the row
  limit when it was missing or too high. Nothing else is rewritten.
- **Mistakes are repaired, attacks are not.** An error goes back to the model; a violation never does.
- **The definitions are the company's, written once.** "Net revenue" means one thing, with its SQL, and
  the model sees it on every question.
- **It learns from the practice questions only.** The checked examples come from the 30 practice
  questions. The 50 test questions are never shown to it.
- **Every call uses temperature 0 and a fixed seed,** and the stored run makes the results repeatable
  on any machine.

## About the data

[`data/generate.py`](data/generate.py) builds the warehouse from a fixed seed and the repository's own
word lists, without Faker, so it never changes unless the generator does. The database file is not
committed: [`data/manifest.json`](data/manifest.json) holds a row count and a checksum for every table,
and the checks fail if a rebuilt warehouse differs.

- A made-up online home and garden shop that sells in eight European regions, with dates from
  1 January 2024 to 30 September 2026.
- 10 tables: about 14,000 customers, 34,000 orders, 69,000 order lines, 1,800 refunds, 2,500
  subscriptions, 240 products in 12 categories, and 27 marketing campaigns, plus the regions and the
  restricted table of personal data.
- 80 analyst questions (30 practice, 50 test) covering filters, joins, time windows, growth from one
  period to the next, top N and the business definitions. Each has a reference query that was checked by
  running it, and its stored result.
- 15 unsafe requests (5 practice, 10 test). Tuning happens on the practice split only.

Every customer, product, campaign and order is fictional, and any resemblance is a coincidence. The
personal data is made up too: email addresses use the reserved example.com, example.org and example.net
domains, and phone numbers use a range set aside for fiction.

---

Built by [I-Team Collage](https://iteamcollage.com): workflow automation for finance, operations and
support teams in Europe, accepted only when it passes a test your own people wrote.
[Book a free 30-minute call](https://calendly.com/dorsa-maskouki/i-team-collage).

Licensed under the [MIT License](LICENSE).
