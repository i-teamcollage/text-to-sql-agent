from datetime import date, datetime
from decimal import Decimal

from text_to_sql_agent.metrics import cells_equal, results_match


def match(ref_columns, ref_rows, columns, rows, ordered=False):
    return results_match(ref_columns, ref_rows, columns, rows, ordered=ordered)


# --- cells ------------------------------------------------------------------------------------------


def test_numbers_of_different_types_are_compared_by_value():
    assert cells_equal(Decimal("1250.50"), 1250.5)
    assert cells_equal(3, Decimal("3.00"))
    assert cells_equal(3, 3.0)


def test_numbers_match_within_a_relative_tolerance_of_one_in_a_million():
    assert cells_equal(1_000_000.0, 1_000_000.9)
    assert not cells_equal(1_000_000.0, 1_000_002.0)
    assert not cells_equal(12.5, 12.6)


def test_a_rounded_number_does_not_match():
    assert not cells_equal(Decimal("12.3456789"), 12.35)


def test_zero_matches_floating_point_noise_around_zero():
    assert cells_equal(0, -1e-12)


def test_a_date_matches_the_same_day_as_a_midnight_timestamp():
    assert cells_equal(date(2026, 1, 1), datetime(2026, 1, 1))
    assert not cells_equal(date(2026, 1, 1), datetime(2026, 1, 1, 12, 0))


def test_text_must_match_exactly_and_null_matches_only_null():
    assert cells_equal("Cookware", "Cookware")
    assert not cells_equal("Cookware", "cookware")
    assert cells_equal(None, None)
    assert not cells_equal(None, 0)


def test_booleans_are_not_numbers():
    assert cells_equal(True, True)
    assert not cells_equal(True, 1)


def test_a_number_written_as_text_is_not_a_number():
    assert not cells_equal(Decimal("10.00"), "10.00")


# --- results ------------------------------------------------------------------------------------------

REF_COLUMNS = ["region_name", "gross_revenue"]
REF_ROWS = [("Nordics", Decimal("100.00")), ("DACH", Decimal("250.00")), ("France", Decimal("80.00"))]


def test_the_same_rows_in_another_order_match_when_order_does_not_matter():
    rows = [("DACH", 250.0), ("France", 80.0), ("Nordics", 100.0)]
    assert match(REF_COLUMNS, REF_ROWS, ["region", "revenue"], rows)


def test_rows_in_another_order_do_not_match_when_the_question_asks_for_an_order():
    rows = [("DACH", 250.0), ("France", 80.0), ("Nordics", 100.0)]
    assert not match(REF_COLUMNS, REF_ROWS, ["region", "revenue"], rows, ordered=True)
    assert match(REF_COLUMNS, REF_ROWS, ["region", "revenue"], list(REF_ROWS), ordered=True)


def test_columns_are_matched_by_content_not_by_name_or_position():
    rows = [(250.0, "DACH"), (80.0, "France"), (100.0, "Nordics")]
    assert match(REF_COLUMNS, REF_ROWS, ["x", "y"], rows)


def test_extra_result_columns_are_allowed():
    rows = [(2, "DACH", 250.0, 31), (4, "France", 80.0, 12), (1, "Nordics", 100.0, 17)]
    assert match(REF_COLUMNS, REF_ROWS, ["region_id", "region_name", "gross_revenue", "orders"], rows)


def test_a_missing_reference_column_is_a_mismatch():
    rows = [("DACH",), ("France",), ("Nordics",)]
    assert not match(REF_COLUMNS, REF_ROWS, ["region_name"], rows)


def test_an_extra_or_missing_row_is_a_mismatch():
    assert not match(REF_COLUMNS, REF_ROWS, REF_COLUMNS, REF_ROWS[:2])
    assert not match(REF_COLUMNS, REF_ROWS, REF_COLUMNS, [*REF_ROWS, ("Italy", Decimal("1.00"))])


def test_duplicate_rows_count_as_a_multiset():
    ref = [("a", 1), ("a", 1), ("b", 2)]
    assert match(["k", "v"], ref, ["k", "v"], [("a", 1), ("b", 2), ("a", 1)])
    assert not match(["k", "v"], ref, ["k", "v"], [("a", 1), ("b", 2), ("b", 2)])


def test_the_column_mapping_must_hold_for_whole_rows_not_column_by_column():
    # Each column alone has the right values, but the pairs are wrong.
    rows = [("Nordics", 250.0), ("DACH", 100.0), ("France", 80.0)]
    assert not match(REF_COLUMNS, REF_ROWS, REF_COLUMNS, rows)


def test_two_reference_columns_cannot_share_one_result_column():
    ref = [(5, 5), (7, 7)]
    assert not match(["a", "b"], ref, ["only"], [(5,), (7,)])
    assert match(["a", "b"], ref, ["x", "y"], [(5, 5), (7, 7)])


def test_a_single_number_answer_matches_whatever_its_column_is_called():
    assert match(["net_revenue"], [(Decimal("1234567.89"),)], ["total"], [(1234567.8900001,)])


def test_near_equal_numbers_that_sort_differently_still_match():
    ref = [("a", 1.0000001), ("b", 1.0000002)]
    rows = [("b", 1.0000002000001), ("a", 1.0000000999999)]
    assert match(["k", "v"], ref, ["k", "v"], rows)


def test_an_empty_reference_matches_an_empty_result():
    assert match(["n"], [], ["n"], [])
