"""app/config.py: the prompt-building helpers, isolated from the full
15k-character prompt they're embedded in."""

from decimal import Decimal

from app import config


def test_speak_dollar_amount_drops_trailing_zero_cents():
    assert config._speak_dollar_amount(Decimal("1000.00")) == "$1000"


def test_speak_dollar_amount_keeps_genuine_cents():
    assert config._speak_dollar_amount(Decimal("266.67")) == "$266.67"


def test_speak_dollar_amount_accepts_a_float():
    """Regression: build_system_prompt's account_balance is typed Decimal
    (the real call path's session.account_balance always genuinely is one),
    but this is still a boundary function -- a caller that hands it a plain
    float (as tests/scenarios/harness.py's TEST_ACCOUNT_BALANCE fixture
    once did, silently, since the old plain `.2f` formatting tolerated it)
    must get a correct answer instead of an AttributeError from calling
    .quantize() on a float."""
    assert config._speak_dollar_amount(500.00) == "$500"
    assert config._speak_dollar_amount(266.67) == "$266.67"


def test_build_system_prompt_accepts_a_float_balance():
    prompt = config.build_system_prompt("Phoebe Buffay", 500.00)
    assert "$500" in prompt


def test_build_system_prompt_contains_no_raw_dot_zero_zero():
    """Every prompt-literal dollar figure goes through _speak_dollar_amount
    -- a stray f"{amount:.2f}" anywhere in build_system_prompt would put a
    trailing ".00" back into a live confirmation turn (Deepgram's TTS reads
    it as "point zero zero")."""
    prompt = config.build_system_prompt("John Callahan", Decimal("1000.00"))
    assert "$1000.00" not in prompt
