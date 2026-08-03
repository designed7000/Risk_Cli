"""Interactive menu tests. Prompt input is scripted, nothing blocks."""
import argparse

import pytest

from riskcli import cli


@pytest.fixture
def answers(monkeypatch):
    """Script the prompts. Each Prompt.ask pops the next scripted reply."""
    scripted = []

    def fake_ask(prompt="", **kwargs):
        if not scripted:
            raise AssertionError(f"menu asked more than expected: {prompt!r}")
        return scripted.pop(0)

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(fake_ask))
    monkeypatch.setattr(cli.console, "clear", lambda: None)
    return scripted


def _defaults():
    return argparse.Namespace(
        period="1y", interval="1d", benchmark="^GSPC", rf=0.0,
        export=None, compare=False, compare_period="3y",
    )


def _run_menu(answers, replies):
    answers.extend(replies)
    return cli.interactive_menu(_defaults())


# --- typing tickers straight at the action prompt -------------------------


def test_a_ticker_typed_at_the_action_prompt_is_accepted(answers):
    ns = _run_menu(answers, ["NVDA", "9"])
    assert ns.tickers == ["NVDA"]


def test_a_weighted_position_typed_directly_is_accepted(answers):
    ns = _run_menu(answers, ["NVDA=0.2 VUAA=0.8", "9"])
    assert ns.tickers == ["NVDA=0.2", "VUAA=0.8"]


def test_several_tickers_typed_directly_become_a_portfolio(answers):
    ns = _run_menu(answers, ["AAPL MSFT TLT", "run"])
    assert ns.tickers == ["AAPL", "MSFT", "TLT"]


def test_a_mistyped_ticker_can_be_corrected(answers):
    # The state table redraws, so a typo is visible and simply retyped.
    ns = _run_menu(answers, ["numbe", "NVDA", "9"])
    assert ns.tickers == ["NVDA"]


def test_lowercase_tickers_are_accepted(answers):
    ns = _run_menu(answers, ["nvda", "9"])
    assert ns.tickers == ["nvda"]


# --- the menu actions still work -----------------------------------------


def test_the_numeric_shortcut_still_opens_the_ticker_field(answers):
    ns = _run_menu(answers, ["1", "AAPL", "9"])
    assert ns.tickers == ["AAPL"]


def test_the_action_name_still_opens_the_ticker_field(answers):
    ns = _run_menu(answers, ["ticker", "AAPL", "run"])
    assert ns.tickers == ["AAPL"]


def test_action_names_are_case_insensitive(answers):
    ns = _run_menu(answers, ["TICKER", "AAPL", "RUN"])
    assert ns.tickers == ["AAPL"]


def test_other_fields_are_still_editable(answers):
    ns = _run_menu(answers, ["2", "5y", "4", "^IXIC", "AAPL", "9"])
    assert ns.period == "5y"
    assert ns.benchmark == "^IXIC"
    assert ns.tickers == ["AAPL"]


def test_the_risk_free_field_normalizes_percentages(answers):
    ns = _run_menu(answers, ["5", "4%", "AAPL", "9"])
    assert ns.rf == pytest.approx(0.04)


def test_compare_toggles(answers):
    ns = _run_menu(answers, ["7", "AAPL", "9"])
    assert ns.compare is True


def test_export_is_none_when_left_blank(answers):
    ns = _run_menu(answers, ["AAPL", "9"])
    assert ns.export is None


# --- guard rails ----------------------------------------------------------


def test_running_with_no_ticker_asks_again(answers):
    # "9" with an empty ticker warns and returns to the menu (one extra
    # prompt for the pause), then a real ticker runs.
    ns = _run_menu(answers, ["9", "", "AAPL", "9"])
    assert ns.tickers == ["AAPL"]


def test_quit_exits(answers, monkeypatch):
    monkeypatch.setattr(cli.Confirm, "ask", staticmethod(lambda *a, **k: True))
    with pytest.raises(SystemExit):
        _run_menu(answers, ["0"])
