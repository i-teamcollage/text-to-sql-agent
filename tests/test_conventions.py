from datetime import date

from text_to_sql_agent.conventions import convention_problem

TODAY = date(2026, 9, 30)


def problem(question, sql):
    return convention_problem(question, sql, TODAY)


def test_last_quarter_must_use_the_quarter_before_this_one():
    wrong = "SELECT count(*) FROM orders WHERE order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-30'"
    message = problem("Orders last quarter?", wrong)
    assert "last quarter" in message and "2026-04-01 to 2026-06-30" in message


def test_last_quarter_with_its_dates_passes():
    right = "SELECT count(*) FROM orders WHERE order_date BETWEEN DATE '2026-04-01' AND DATE '2026-06-30'"
    assert problem("Orders last quarter?", right) is None


def test_an_exclusive_end_date_is_fine():
    right = "SELECT count(*) FROM orders WHERE order_date >= DATE '2026-04-01' AND order_date < DATE '2026-07-01'"
    assert problem("Orders last quarter?", right) is None


def test_open_periods_only_need_their_start_because_the_data_ends_today():
    assert problem("Revenue this quarter?", "SELECT 1 FROM orders WHERE order_date >= DATE '2026-07-01'") is None
    assert problem("Revenue this year?", "SELECT 1 FROM orders WHERE order_date >= '2026-01-01'") is None


def test_last_n_days_start_on_the_right_day():
    assert "2026-09-01" in problem("How many orders came in over the last 30 days?", "SELECT count(*) FROM orders")
    right = "SELECT count(*) FROM orders WHERE order_date BETWEEN DATE '2026-09-01' AND DATE '2026-09-30'"
    assert problem("How many orders came in over the last 30 days?", right) is None


def test_last_month_and_last_year_need_both_ends():
    assert problem("Sign-ups last month", "SELECT count(*) FROM customers WHERE signup_date >= '2026-08-01'")
    right = "SELECT count(*) FROM customers WHERE signup_date BETWEEN '2025-01-01' AND '2025-12-31'"
    assert problem("Sign-ups last year", right) is None


def test_how_many_orders_must_be_a_count():
    message = problem("Number of refunds paid out?", "SELECT sum(amount) FROM refunds")
    assert "COUNT" in message
    assert problem("Number of refunds paid out?", "SELECT count(*) FROM refunds") is None


def test_units_sold_is_not_a_count_of_rows():
    assert problem("Units sold per category", "SELECT sum(quantity) FROM order_items") is None


def test_growth_must_subtract_before_dividing():
    ratio = "SELECT count(*) FILTER (WHERE order_date >= '2026-08-01') * 100.0 / count(*) AS growth FROM orders"
    assert "subtract" in problem("Year-over-year growth in orders", ratio)


def test_dashes_inside_dates_are_not_a_subtraction():
    ratio = "SELECT a * 100.0 / b AS growth FROM t WHERE d BETWEEN '2026-08-01' AND '2026-08-31'"
    assert problem("Growth in orders for August 2026", ratio)


def test_growth_with_a_subtraction_passes():
    right = "SELECT (a - b) * 100.0 / b AS growth_pct FROM t"
    assert problem("Growth in orders for August 2026", right) is None


def test_lag_and_a_filter_in_the_same_select_lose_the_previous_month():
    wrong = (
        "WITH m AS (SELECT date_trunc('month', signup_date) AS month, count(*) AS n FROM customers GROUP BY 1) "
        "SELECT month, (n - LAG(n) OVER (ORDER BY month)) * 100.0 / LAG(n) OVER (ORDER BY month) AS growth "
        "FROM m WHERE month IN (DATE '2026-04-01', DATE '2026-05-01')"
    )
    assert "LAG" in problem("Month-over-month change in sign-ups for April and May 2026", wrong)


def test_lag_filtered_with_qualify_or_outside_passes():
    qualify = (
        "WITH m AS (SELECT date_trunc('month', signup_date) AS month, count(*) AS n FROM customers GROUP BY 1) "
        "SELECT month, (n - LAG(n) OVER (ORDER BY month)) * 100.0 / LAG(n) OVER (ORDER BY month) AS growth "
        "FROM m QUALIFY month >= DATE '2026-04-01'"
    )
    outside = (
        "SELECT * FROM (SELECT month, n - LAG(n) OVER (ORDER BY month) AS change FROM m) "
        "WHERE month >= DATE '2026-04-01'"
    )
    assert problem("Month-over-month change in sign-ups for April 2026", qualify) is None
    assert problem("Month-over-month change in sign-ups for April 2026", outside) is None


def test_a_question_without_relative_dates_or_counts_has_nothing_to_check():
    assert problem("Gross revenue by department for 2025", "SELECT department, sum(x) FROM t GROUP BY 1") is None
