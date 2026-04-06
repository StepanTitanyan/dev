import pytest
from rise.workers.funding_circle.payloads import (
    _to_cents,
    _map_full_time_employees_to_fc_value,
    _map_profit_amount_to_fc_profit_band,
    _map_turnover_amount_to_fc_turnover_band,
    build_company_search_params,
    build_eligibility_payload,
)


# ---------------------------------------------------------------------------
# _to_cents
# ---------------------------------------------------------------------------

def test_to_cents_converts_float():
    assert _to_cents(100.50) == 10050


def test_to_cents_converts_int():
    assert _to_cents(500) == 50000


def test_to_cents_none_returns_zero():
    assert _to_cents(None) == 0


def test_to_cents_empty_string_returns_zero():
    assert _to_cents("") == 0


# ---------------------------------------------------------------------------
# Employee band mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("employees,expected", [
    (0,   0),
    (1,   1),
    (5,   1),
    (6,   6),
    (10,  6),
    (11,  11),
    (20,  11),
    (21,  21),
    (50,  21),
    (51,  51),
    (249, 51),
    (250, 250),
    (500, 250),
])
def test_employee_band_mapping(employees, expected):
    assert _map_full_time_employees_to_fc_value(employees) == expected


def test_employee_band_none_returns_none():
    assert _map_full_time_employees_to_fc_value(None) is None


# ---------------------------------------------------------------------------
# Profit band mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profit,expected", [
    (0,        25000),
    (50000,    25000),
    (50001,    75000),
    (100000,   75000),
    (100001,   500000),
    (1000000,  500000),
    (1000001,  1000000),
    (-1,       -25000),
    (-50000,   -25000),
    (-50001,   -75000),
    (-100000,  -75000),
    (-100001,  -100000),
])
def test_profit_band_mapping(profit, expected):
    assert _map_profit_amount_to_fc_profit_band(profit) == expected


def test_profit_band_none_returns_none():
    assert _map_profit_amount_to_fc_profit_band(None) is None


# ---------------------------------------------------------------------------
# Turnover band mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("turnover,expected", [
    (50000,    25000),
    (100000,   75000),
    (250000,   175000),
    (500000,   375000),
    (1000000,  750000),
    (2000000,  1500000),
    (5000000,  3500000),
    (6000000,  6000000),
])
def test_turnover_band_mapping(turnover, expected):
    assert _map_turnover_amount_to_fc_turnover_band(turnover) == expected


def test_turnover_band_none_returns_none():
    assert _map_turnover_amount_to_fc_turnover_band(None) is None


# ---------------------------------------------------------------------------
# build_company_search_params
# ---------------------------------------------------------------------------

def test_company_search_uses_company_number_as_term():
    payload = {"company": {"business_structure": "limited", "company_number": "12345678"}}
    result = build_company_search_params(payload)
    assert result["search_string"] == "12345678"
    assert result["type"] == "limited"


def test_company_search_prefers_search_term_over_number():
    payload = {"company": {
        "business_structure": "limited",
        "company_search_term": "Acme",
        "company_number": "12345678",
    }}
    result = build_company_search_params(payload)
    assert result["search_string"] == "Acme"


def test_company_search_llp_maps_to_llp_type():
    payload = {"company": {
        "business_structure": "limited-liability-partnership",
        "company_name": "Some LLP",
    }}
    result = build_company_search_params(payload)
    assert result["type"] == "limited-liability-partnership"


# ---------------------------------------------------------------------------
# build_eligibility_payload
# ---------------------------------------------------------------------------

def test_eligibility_payload_converts_amount_to_cents():
    salesforce_payload = {
        "commission": 2.5,
        "loan_request": {"requested_amount_gbp": 50000, "term_requested_months": 24},
        "company": {
            "business_structure": "limited",
            "company_name": "Acme Ltd",
            "company_number": "12345678",
            "client_email": "finance@acme.co.uk",
        },
    }
    company = {"business_structure": "limited", "company_name": "Acme Ltd", "company_number": "12345678"}
    result = build_eligibility_payload(company, salesforce_payload)
    assert result["amount_requested_cents"] == 5000000
    assert result["term_requested_months"] == 24
    assert result["commission"] == 2.5
