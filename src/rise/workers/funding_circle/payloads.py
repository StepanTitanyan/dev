from typing import Any


BUSINESS_STRUCTURE_MAP = {
    "limited": "limited-company",
    "limited-company": "limited-company",
    "limited-liability-partnership": "limited-liability-partnership",
    "llp": "limited-liability-partnership",
    "ltd": "limited-company"}


def _company_section(salesforce_payload: dict) -> dict:
    return salesforce_payload.get("company") or {}


def _loan_request_section(salesforce_payload: dict) -> dict:
    return salesforce_payload.get("loan_request") or {}


def _applicant_section(salesforce_payload: dict) -> dict:
    return salesforce_payload.get("applicant") or {}


def _loan_purpose_section(salesforce_payload: dict) -> dict:
    return salesforce_payload.get("loan_purpose") or {}


def _business_performance_section(salesforce_payload: dict) -> dict:
    return salesforce_payload.get("business_performance") or {}


def _owners_section(salesforce_payload: dict) -> list[dict]:
    return salesforce_payload.get("resolved_executive_business_owners") or []


def _to_cents(amount: Any) -> int:
    if amount in (None, ""):
        return 0
    return int(round(float(amount) * 100))


def _normalise_business_structure(value: str) -> str:
    return BUSINESS_STRUCTURE_MAP.get(value.lower().strip(), value)


def _derive_company_search_type(business_structure: str) -> str:
    normalised = _normalise_business_structure(business_structure)
    if normalised == "limited-liability-partnership":
        return "limited-liability-partnership"
    return "limited"


def _map_full_time_employees_to_fc_value(full_time_employees: int | None) -> int | None:
    if full_time_employees is None:
        return None

    employees = int(full_time_employees)

    if employees <= 0:
        return 0
    if employees <= 5:
        return 1
    if employees <= 10:
        return 6
    if employees <= 20:
        return 11
    if employees <= 50:
        return 21
    if employees <= 249:
        return 51
    return 250


def _map_profit_amount_to_fc_profit_band(profit_amount: int | None) -> int | None:
    if profit_amount is None:
        return None

    amount = int(profit_amount)

    if amount >= 0:
        if amount <= 50000:
            return 25000
        if amount <= 100000:
            return 75000
        if amount <= 1000000:
            return 500000
        return 1000000

    absolute_amount = abs(amount)

    if absolute_amount <= 50000:
        return -25000
    if absolute_amount <= 100000:
        return -75000
    return -100000


def _map_turnover_amount_to_fc_turnover_band(turnover_amount: float | int | None) -> int | None:
    if turnover_amount is None:
        return None

    amount = float(turnover_amount)

    if amount <= 50000:
        return 25000
    if amount <= 100000:
        return 75000
    if amount <= 250000:
        return 175000
    if amount <= 500000:
        return 375000
    if amount <= 1000000:
        return 750000
    if amount <= 2000000:
        return 1500000
    if amount <= 5000000:
        return 3500000
    return int(round(amount))


def _debt_taken_in_last_year_cents(business_performance: dict) -> int:
    debt_amount = business_performance.get("new_debt_last_12_months_amount")
    has_large_borrowing = business_performance.get("has_taken_more_than_25000_borrowing_last_12_months")

    if debt_amount not in (None, ""):
        return _to_cents(debt_amount)

    if has_large_borrowing is False:
        return 0

    return 0


def _normalize_ni_protocol_answers(
    company_established_or_registered_in_northern_ireland,
    company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market,
):
    if company_established_or_registered_in_northern_ireland is False:
        return False, None

    return (
        company_established_or_registered_in_northern_ireland,
        company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market,
    )


def build_company_search_params(salesforce_payload: dict) -> dict:
    company = _company_section(salesforce_payload)

    business_structure = company.get("business_structure") or ""
    company_type = _derive_company_search_type(business_structure)

    company_search_term = (
        company.get("company_search_term")
        or company.get("company_number")
        or company.get("company_name")
        or ""
    )

    return {
        "search_string": company_search_term,
        "type": company_type,
    }


def build_eligibility_payload(company: dict, salesforce_payload: dict):
    loan_request = _loan_request_section(salesforce_payload)
    company_section = _company_section(salesforce_payload)

    company_name = company.get("company_name") or company_section.get("company_name") or ""
    company_codas_id = company.get("company_codas_id") or company_section.get("company_codas_id") or ""
    unique_company_identifier = (
        company.get("company_number")
        or company.get("unique_company_identifier")
        or company_section.get("unique_company_identifier")
        or company_section.get("company_number")
        or ""
    )

    amount_requested_cents = _to_cents(loan_request.get("requested_amount_gbp"))

    raw_structure = (
        company.get("business_structure")
        or company_section.get("business_structure")
        or "")
    business_structure = _normalise_business_structure(raw_structure)

    return {
        "amount_requested_cents": amount_requested_cents,
        "business_structure": business_structure,
        "commission": salesforce_payload.get("commission"),
        "company_name": company_name,
        "company_codas_id": company_codas_id,
        "email": company_section.get("client_email"),
        "term_requested_months": loan_request.get("term_requested_months"),
        "unique_company_identifier": unique_company_identifier,
    }


def build_applicant_detail_payload(salesforce_payload: dict):
    applicant = _applicant_section(salesforce_payload)

    payload = {
        "first_name": applicant["first_name"],
        "last_name": applicant["last_name"],
    }

    return {"payload": payload}


def build_loan_application_details_payload(salesforce_payload: dict):
    loan_purpose = _loan_purpose_section(salesforce_payload)
    main_loan_purpose = loan_purpose.get("loan_purpose")

    payload = {
        "loan_purpose": main_loan_purpose,
        "loan_purpose_details": {
            "property": loan_purpose.get("loan_purpose_details_property", False),
            "new_sector": loan_purpose.get("loan_purpose_details_new_sector", False),
            "personal_debt": loan_purpose.get("loan_purpose_details_personal_debt", False),
            "outside_uk": loan_purpose.get("loan_purpose_details_outside_uk", False),
            "not_for_applicant": loan_purpose.get("loan_purpose_details_not_for_applicant", False),
        },
    }

    if main_loan_purpose == "Fund vehicle, equipment or machinery":
        payload["loan_for_assets"] = loan_purpose.get("loan_for_assets")

    return {"payload": payload}


def build_company_performance_payload(salesforce_payload: dict):
    business_performance = _business_performance_section(salesforce_payload)

    ni_registered, ni_electricity = _normalize_ni_protocol_answers(
        business_performance.get("company_established_or_registered_in_northern_ireland"),
        business_performance.get("company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market"),
    )

    payload = {
        "profit_band": _map_profit_amount_to_fc_profit_band(business_performance.get("profit_band")),
        "turnover_band": _map_turnover_amount_to_fc_turnover_band(business_performance.get("self_stated_turnover")),
        "self_stated_industry": business_performance.get("self_stated_industry"),
        "self_stated_turnover": business_performance.get("self_stated_turnover"),
        "self_stated_turnover_for_2019": business_performance.get("self_stated_turnover_for_2019") or 0,
        "full_time_employees": _map_full_time_employees_to_fc_value(business_performance.get("full_time_employees")),
        "overdraft_facility_exists": business_performance.get("overdraft_facility_exists"),
        "company_established_or_registered_in_northern_ireland": ni_registered,
        "company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market": ni_electricity,
        "debt_taken_in_last_year_cents": _debt_taken_in_last_year_cents(business_performance),
    }

    if business_performance.get("overdraft_facility_exists") is True:
        payload["overdraft_current_usage_cents"] = _to_cents(business_performance.get("overdraft_current_usage_amount"))
        payload["overdraft_maximum_available_cents"] = _to_cents(business_performance.get("overdraft_limit_amount"))

    return {"payload": payload}


def build_contact_details_payload(salesforce_payload: dict):
    applicant = _applicant_section(salesforce_payload)
    return {"payload": {"mobile_number": applicant["mobile_number"]}}


def build_executive_business_owners_payload(salesforce_payload: dict):
    owners = _owners_section(salesforce_payload)
    executive_business_owners = []

    for index, owner in enumerate(owners):
        if not owner.get("registry_name") or not owner.get("fc_person_id"):
            continue

        owner_address = owner.get("address") or {}

        previous_addresses = []
        for previous_address in owner.get("previous_addresses") or []:
            previous_addresses.append({
                "house_number_or_name": previous_address.get("house_number_or_name"),
                "street": previous_address.get("street"),
                "city": previous_address.get("city"),
                "postcode": previous_address.get("postcode"),
            })

        executive_business_owners.append({
            "id": owner.get("id") or f"owner-{index + 1}",
            "registry_name": owner.get("registry_name"),
            "first_name": owner.get("first_name"),
            "last_name": owner.get("last_name"),
            "percent_shares_held": owner.get("percent_shares_held"),
            "fc_person_id": owner.get("fc_person_id"),
            "date_of_birth": owner.get("date_of_birth"),
            "address": {
                "house_number_or_name": owner_address.get("house_number_or_name"),
                "street": owner_address.get("street"),
                "city": owner_address.get("city"),
                "postcode": owner_address.get("postcode"),
            },
            "previous_addresses": previous_addresses,
        })

    return {"payload": {"executive_business_owners": executive_business_owners}}