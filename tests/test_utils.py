"""Formatting helper tests."""
from riskcli import utils


def test_missing_number_renders_as_na():
    assert utils.human_number(None) == "n/a"


def test_small_numbers_keep_no_suffix():
    assert utils.human_number(5) == "5.00"


def test_thousands_millions_billions():
    assert utils.human_number(1_500) == "1.50K"
    assert utils.human_number(2_400_000) == "2.40M"
    assert utils.human_number(3_120_000_000) == "3.12B"


def test_negative_numbers_keep_their_sign():
    assert utils.human_number(-2_500) == "-2.50K"


def test_sparkline_of_nothing_is_empty():
    assert utils.sparkline([]) == ""


def test_flat_series_uses_the_lowest_block():
    assert utils.sparkline([7, 7, 7]) == "▁▁▁"


def test_sparkline_spans_the_full_block_range():
    line = utils.sparkline([1, 2, 3, 4])
    assert len(line) == 4
    assert line[0] == "▁" and line[-1] == "█"
