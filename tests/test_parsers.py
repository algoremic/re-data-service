from __future__ import annotations

from decimal import Decimal

from re_data.ingest.parsers import (
    cell_str,
    date_from_filename,
    key_tuple,
    norm,
    parse_date_any,
    parse_decimal,
    parse_int,
)


def test_norm_strips_and_lowercases():
    assert norm("  Dubai Marina  ") == "dubai marina"


# ---------------------------------------------------------------------------
# cell_str — formula injection guard (ADR-022 parity with re-ml-service)
# ---------------------------------------------------------------------------

def test_cell_str_strips_equals_prefix():
    assert cell_str('=HYPERLINK("evil","click")') == 'HYPERLINK("evil","click")'


def test_cell_str_strips_plus_prefix():
    assert cell_str("+123") == "123"


def test_cell_str_strips_at_prefix():
    assert cell_str("@cmd") == "cmd"


def test_cell_str_strips_minus_prefix():
    assert cell_str("-1") == "1"


def test_cell_str_strips_leading_whitespace_after_prefix():
    assert cell_str("= SUM(A1:A10)") == "SUM(A1:A10)"


def test_cell_str_normal_string_unchanged():
    assert cell_str("Dubai Marina") == "Dubai Marina"


def test_cell_str_numeric_string_unchanged():
    assert cell_str("100.5") == "100.5"


def test_cell_str_none_returns_empty():
    assert cell_str(None) == ""


def test_cell_str_empty_string_returns_empty():
    assert cell_str("") == ""


def test_cell_str_null_literal_returns_empty():
    assert cell_str("null") == ""


def test_cell_str_int_passthrough():
    assert cell_str(42) == "42"


def test_cell_str_float_whole_number():
    assert cell_str(3.0) == "3"


def test_cell_str_bool_true():
    assert cell_str(True) == "true"


def test_cell_str_bool_false():
    assert cell_str(False) == "false"


def test_parse_decimal_handles_commas():
    assert parse_decimal("1,076,391") == Decimal("1076391")


def test_parse_int_extracts_leading_digits():
    assert parse_int("2 BR") == 2


def test_parse_date_any_iso():
    assert parse_date_any("2026-02-23") == "2026-02-23"


def test_date_from_filename():
    assert date_from_filename("transactions-2026-02-23.csv") == "2026-02-23"


def test_key_tuple_defaults():
    assert key_tuple(None, "Marina", None, "flat", 2) == (
        "dubai",
        "marina",
        "",
        "flat",
        2,
    )
