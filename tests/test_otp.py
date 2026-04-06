import pytest
from rise.otp.webhook import extract_otp_from_text


@pytest.mark.parametrize("message,expected", [
    ("Your code is 123456",           "123456"),
    ("Use 4521 to verify",            "4521"),
    ("OTP: 87654321",                 "87654321"),
    ("FundingCircle code: 7788",      "7788"),
    ("Your one-time passcode is 9900","9900"),
    ("Code 123456 expires in 5 mins", "123456"),
])
def test_extracts_otp_from_message(message, expected):
    assert extract_otp_from_text(message) == expected


def test_returns_none_when_no_number():
    assert extract_otp_from_text("Hello there") is None


def test_returns_none_for_empty_string():
    assert extract_otp_from_text("") is None


def test_returns_none_for_none_input():
    assert extract_otp_from_text(None) is None


def test_ignores_numbers_shorter_than_4_digits():
    assert extract_otp_from_text("Call us on 123") is None


def test_picks_first_match_when_multiple_numbers():
    # Regex returns first match — consistent and predictable
    result = extract_otp_from_text("Code 1234 or 5678")
    assert result == "1234"
