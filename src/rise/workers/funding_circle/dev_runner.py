from rise.workers.funding_circle.workflow import run_application_workflow

if __name__ == "__main__":
    salesforce_payload = {
    "salesforce_record_id": "a0B8d00000XYZ123EAG",
    "submitted_at": "2026-03-18T17:35:00Z",
    "loan_request": {
        "requested_amount_gbp": 50000,
        "amount_requested_cents": 5000000,
        "term_requested_months": 36,
    },
    "commission": 2.5,
    "company": {
        "business_structure": "limited-company",
        "type": "limited-company",
        "company_search_term": "Google Cars UK Limited",
        "company_name": "GOOGLE CARS UK LIMITED",
        "company_number": "16239033",
        "company_codas_id": "9cf1f452-e99a-11ef-8a72-ac7f9324e034",
        "unique_company_identifier": "16239033",
        "registered_address": "46 PERIMAN CLOSE, NEWMARKET",
        "registered_postcode": "CB8 0SU",
        "client_is_director_of_business": True,
        "client_email": "system@example.com",
    },
    "applicant": {
        "first_name": "name",
        "last_name": "suename",
        "mobile_number": "+447700900123",
    },
    "loan_purpose": {
        "loan_purpose": "Working capital",
        "loan_for_assets": None,
        "loan_purpose_details_property": False,
        "loan_purpose_details_new_sector": False,
        "loan_purpose_details_personal_debt": False,
        "loan_purpose_details_outside_uk": False,
        "loan_purpose_details_not_for_applicant": False,
    },
    "business_performance": {
        "self_stated_industry": "Finance",
        "full_time_employee_band": "11-20",
        "full_time_employees": 11,
        "company_established_or_registered_in_northern_ireland": True,
        "company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market": True,
        "self_stated_turnover": 500000,
        "self_stated_turnover_for_2019": 66000,
        "profit_or_loss": "loss",
        "profit_loss_band_label": "Loss: £50k–£100k",
        "profit_band": -75000,
        "overdraft_facility_exists": True,
        "overdraft_limit_amount": 50000,
        "overdraft_current_usage_amount": 20000,
        "has_taken_more_than_25000_borrowing_last_12_months": False,
        "new_debt_last_12_months_amount": None,
    },
    "executive_business_owners": [
        {
            "registry_name": "MARTIN KERRY",
            "first_name": "Martin",
            "last_name": "Kerry",
            "percent_shares_held": 73.56,
            "date_of_birth": "1980-04-12",
            "address_house_number_or_name": "35",
            "address_street": "Ballards Lane",
            "address_city": "London",
            "address_postcode": "N3 1XW",
            "previous_addresses": [],
            "fc_person_id": None,
        }
    ],
    "system": {
        "application_id": None,
    },
}


    result = run_application_workflow(salesforce_payload=salesforce_payload, step="get_loan_application_details", application_id= "52383c42-5fd4-4f93-b234-05afc6791671")
    print("\n=== FINAL RESULT ===")
    print(result)