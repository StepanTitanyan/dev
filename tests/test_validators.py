import pytest
from pydantic import ValidationError
from rise.api.validators.funding_circle import FundingCirclePayload


VALID_PAYLOAD = {
    "salesforce_record_id": "a0B5g00000XyZabEAF",
    "commission": 2.5,
    "loan_request": {
        "requested_amount_gbp": 50000,
        "term_requested_months": 24,
    },
    "company": {
        "business_structure": "limited",
        "company_number": "12345678",
        "client_email": "finance@acme.co.uk",
    },
    "applicant": {
        "first_name": "John",
        "last_name": "Smith",
        "mobile_number": "07700900000",
    },
    "loan_purpose": {
        "loan_purpose": "Working capital",
    },
    "business_performance": {
        "self_stated_industry": "Technology",
        "full_time_employees": 10,
        "company_established_or_registered_in_northern_ireland": False,
        "self_stated_turnover": 500000,
        "profit_band": 75000,
        "overdraft_facility_exists": False,
    },
}


def _make(**overrides):
    import copy
    payload = copy.deepcopy(VALID_PAYLOAD)
    payload.update(overrides)
    return payload


def test_valid_payload_passes():
    result = FundingCirclePayload(**VALID_PAYLOAD)
    assert result.salesforce_record_id == "a0B5g00000XyZabEAF"


def test_missing_salesforce_record_id_fails():
    data = _make()
    del data["salesforce_record_id"]
    with pytest.raises(ValidationError):
        FundingCirclePayload(**data)


def test_empty_salesforce_record_id_fails():
    with pytest.raises(ValidationError):
        FundingCirclePayload(**_make(salesforce_record_id="  "))


def test_negative_commission_fails():
    with pytest.raises(ValidationError):
        FundingCirclePayload(**_make(commission=-1))


def test_zero_loan_amount_fails():
    with pytest.raises(ValidationError):
        FundingCirclePayload(**_make(loan_request={"requested_amount_gbp": 0, "term_requested_months": 24}))


def test_zero_term_fails():
    with pytest.raises(ValidationError):
        FundingCirclePayload(**_make(loan_request={"requested_amount_gbp": 50000, "term_requested_months": 0}))


def test_company_requires_at_least_one_identifier():
    data = _make()
    data["company"] = {
        "business_structure": "limited",
        "client_email": "finance@acme.co.uk",
        # no company_number, company_name, or company_search_term
    }
    with pytest.raises(ValidationError):
        FundingCirclePayload(**data)


def test_company_name_alone_is_sufficient():
    data = _make()
    data["company"] = {
        "business_structure": "limited",
        "company_name": "Acme Ltd",
        "client_email": "finance@acme.co.uk",
    }
    FundingCirclePayload(**data)


def test_empty_applicant_first_name_fails():
    data = _make()
    data["applicant"] = {"first_name": "", "last_name": "Smith", "mobile_number": "07700900000"}
    with pytest.raises(ValidationError):
        FundingCirclePayload(**data)


def test_loan_purpose_assets_required_when_fund_vehicle():
    data = _make()
    data["loan_purpose"] = {
        "loan_purpose": "Fund vehicle, equipment or machinery",
        # loan_for_assets missing
    }
    with pytest.raises(ValidationError):
        FundingCirclePayload(**data)


def test_loan_purpose_assets_provided_when_fund_vehicle():
    data = _make()
    data["loan_purpose"] = {
        "loan_purpose": "Fund vehicle, equipment or machinery",
        "loan_for_assets": "Van",
    }
    FundingCirclePayload(**data)


def test_overdraft_requires_amounts_when_true():
    data = _make()
    data["business_performance"] = {
        **VALID_PAYLOAD["business_performance"],
        "overdraft_facility_exists": True,
        # overdraft_limit_amount and overdraft_current_usage_amount missing
    }
    with pytest.raises(ValidationError):
        FundingCirclePayload(**data)


def test_overdraft_valid_when_amounts_provided():
    data = _make()
    data["business_performance"] = {
        **VALID_PAYLOAD["business_performance"],
        "overdraft_facility_exists": True,
        "overdraft_limit_amount": 10000,
        "overdraft_current_usage_amount": 5000,
    }
    FundingCirclePayload(**data)


def test_mock_defaults_to_false():
    result = FundingCirclePayload(**VALID_PAYLOAD)
    assert result.mock is False


def test_executive_business_owners_defaults_to_empty():
    result = FundingCirclePayload(**VALID_PAYLOAD)
    assert result.executive_business_owners == []


def test_content_version_ids_defaults_to_empty():
    result = FundingCirclePayload(**VALID_PAYLOAD)
    assert result.content_version_ids == []
