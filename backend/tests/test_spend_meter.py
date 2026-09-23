import pytest

from app.providers.spend import Charge, SpendCapReached, SpendMeter, estimate_tokens, price


def test_meter_refuses_what_does_not_fit() -> None:
    meter = SpendMeter(budget_usd=1.0)
    meter.reserve(1.0)  # exactly fits
    meter.charge(Charge("m", 10, 0.75, estimated=False))
    assert meter.remaining_usd == pytest.approx(0.25)
    meter.reserve(0.25)
    with pytest.raises(SpendCapReached):
        meter.reserve(0.26)


def test_take_hands_over_pending_charges_once() -> None:
    meter = SpendMeter(budget_usd=1.0)
    charges = [Charge("m", 1, 0.1, estimated=False), Charge("m", 2, 0.2, estimated=True)]
    for charge in charges:
        meter.charge(charge)
    assert meter.take() == charges
    assert meter.take() == []
    assert meter.spent_usd == pytest.approx(0.3)  # taking doesn't refund


def test_a_spent_budget_refuses_everything_but_free_requests() -> None:
    meter = SpendMeter(budget_usd=0.0)
    meter.reserve(0.0)
    with pytest.raises(SpendCapReached):
        meter.reserve(1e-9)


def test_estimates() -> None:
    assert estimate_tokens(["abcd", "ef"], chars_per_token=3) == 2
    assert estimate_tokens([], chars_per_token=3) == 0
    assert price(2_000_000, usd_per_mtok=0.01) == pytest.approx(0.02)
