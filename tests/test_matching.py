import pytest
from rise.workers.funding_circle.matching.owner import (
    extract_majority_executive_business_owners,
    find_best_fc_owner_match,
    build_resolved_executive_business_owner,
)


FC_OWNERS = [
    {"registry_name": "JOHN SMITH", "fc_person_id": "fc-001", "percent_shares_held": 60},
    {"registry_name": "JANE DOE",   "fc_person_id": "fc-002", "percent_shares_held": 40},
]


# ---------------------------------------------------------------------------
# extract_majority_executive_business_owners
# ---------------------------------------------------------------------------

def test_extracts_owners_with_50_percent_or_more():
    next_action = {"attributes": {"potential_executive_business_owners": FC_OWNERS}}
    result = extract_majority_executive_business_owners(next_action)
    assert len(result) == 1
    assert result[0]["fc_person_id"] == "fc-001"


def test_returns_empty_when_no_majority_owners():
    owners = [
        {"registry_name": "A", "fc_person_id": "fc-001", "percent_shares_held": 30},
        {"registry_name": "B", "fc_person_id": "fc-002", "percent_shares_held": 30},
    ]
    next_action = {"attributes": {"potential_executive_business_owners": owners}}
    assert extract_majority_executive_business_owners(next_action) == []


def test_returns_empty_when_no_owners_in_response():
    assert extract_majority_executive_business_owners({}) == []


# ---------------------------------------------------------------------------
# find_best_fc_owner_match
# ---------------------------------------------------------------------------

def test_matches_by_fc_person_id():
    source = {"fc_person_id": "fc-001", "registry_name": "anything"}
    result = find_best_fc_owner_match(source, FC_OWNERS)
    assert result["success"] is True
    assert result["match_type"] == "fc_person_id"
    assert result["fc_owner"]["fc_person_id"] == "fc-001"


def test_matches_by_exact_name():
    source = {"registry_name": "JOHN SMITH"}
    result = find_best_fc_owner_match(source, FC_OWNERS)
    assert result["success"] is True
    assert result["match_type"] == "exact_name"


def test_matches_by_fuzzy_name():
    source = {"registry_name": "Jon Smith"}  # typo
    result = find_best_fc_owner_match(source, FC_OWNERS)
    assert result["success"] is True
    assert result["fc_owner"]["registry_name"] == "JOHN SMITH"


def test_fails_when_no_match():
    source = {"registry_name": "COMPLETELY DIFFERENT PERSON"}
    result = find_best_fc_owner_match(source, FC_OWNERS)
    assert result["success"] is False


def test_fails_when_source_has_no_registry_name():
    result = find_best_fc_owner_match({}, FC_OWNERS)
    assert result["success"] is False


# ---------------------------------------------------------------------------
# build_resolved_executive_business_owner
# ---------------------------------------------------------------------------

def test_builds_resolved_owner_with_nested_address():
    source = {
        "first_name": "John",
        "last_name": "Smith",
        "date_of_birth": "1980-01-15",
        "address_house_number_or_name": "10",
        "address_street": "High Street",
        "address_city": "London",
        "address_postcode": "SW1A 1AA",
        "previous_addresses": [],
    }
    fc_owner = {"id": "fc-001", "registry_name": "JOHN SMITH", "fc_person_id": "fc-001", "percent_shares_held": 60}
    result = build_resolved_executive_business_owner(source, fc_owner)

    assert result["first_name"] == "John"
    assert result["address"]["house_number_or_name"] == "10"
    assert result["address"]["street"] == "High Street"
    assert result["address"]["city"] == "London"
    assert result["address"]["postcode"] == "SW1A 1AA"
    assert result["fc_person_id"] == "fc-001"


def test_maps_previous_address_fields_correctly():
    source = {
        "first_name": "John",
        "last_name": "Smith",
        "previous_addresses": [
            {
                "address_house_number_or_name": "5",
                "address_street": "Old Road",
                "address_city": "Manchester",
                "address_postcode": "M1 1AA",
            }
        ],
    }
    fc_owner = {"id": "fc-001", "registry_name": "JOHN SMITH", "fc_person_id": "fc-001", "percent_shares_held": 60}
    result = build_resolved_executive_business_owner(source, fc_owner)

    assert result["previous_addresses"][0]["house_number_or_name"] == "5"
    assert result["previous_addresses"][0]["street"] == "Old Road"
