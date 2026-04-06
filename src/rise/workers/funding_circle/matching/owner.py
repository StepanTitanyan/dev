from rapidfuzz import fuzz


def normalize_registry_name(value: str | None):
    return " ".join(((value or "").strip().upper()).split())


def extract_majority_executive_business_owners(next_action_response: dict):
    potential_owners = (next_action_response.get("attributes", {}).get("potential_executive_business_owners") or [])
    majority_owners = []
    for owner in potential_owners:
        percent_shares_held = owner.get("percent_shares_held") or 0
        try:
            if float(percent_shares_held) >= 50:
                majority_owners.append(owner)
        except (TypeError, ValueError):
            continue
    return majority_owners


def find_best_fc_owner_match(source_owner: dict, fc_owners: list[dict], min_score: int = 90):
    source_fc_person_id = source_owner.get("fc_person_id")
    if source_fc_person_id:
        for fc_owner in fc_owners:
            if fc_owner.get("fc_person_id") == source_fc_person_id:
                return {
                    "success": True,
                    "match_type": "fc_person_id",
                    "score": 100,
                    "fc_owner": fc_owner}

    source_name = normalize_registry_name(source_owner.get("registry_name"))
    if not source_name:
        return {
            "success": False,
            "message": "Source owner does not contain registry_name"}

    exact_name_matches = []
    for fc_owner in fc_owners:
        fc_name = normalize_registry_name(fc_owner.get("registry_name"))
        if fc_name and fc_name == source_name:
            exact_name_matches.append(fc_owner)

    if len(exact_name_matches) == 1:
        return {
            "success": True,
            "match_type": "exact_name",
            "score": 100,
            "fc_owner": exact_name_matches[0]}

    if len(exact_name_matches) > 1:
        return {
            "success": False,
            "message": f"Multiple exact FC owner matches found for source owner '{source_name}'"}

    scored_matches = []
    for fc_owner in fc_owners:
        fc_name = normalize_registry_name(fc_owner.get("registry_name"))
        if not fc_name:
            continue

        score = fuzz.ratio(source_name, fc_name)
        scored_matches.append({
            "score": score,
            "fc_owner": fc_owner,
            "fc_name": fc_name})

    if not scored_matches:
        return {
            "success": False,
            "message": f"No FC owners available to match source owner '{source_name}'"}

    scored_matches.sort(key=lambda item: item["score"], reverse=True)
    best_match = scored_matches[0]
    second_best_match = scored_matches[1] if len(scored_matches) > 1 else None

    if best_match["score"] < min_score:
        return {
            "success": False,
            "message": f"No strong FC owner match found for '{source_name}'. Best score was {best_match['score']} against '{best_match['fc_name']}'"}

    if second_best_match and best_match["score"] - second_best_match["score"] < 3:
        return {
            "success": False,
            "message": f"Ambiguous FC owner match for '{source_name}'. Best='{best_match['fc_name']}' ({best_match['score']}), second='{second_best_match['fc_name']}' ({second_best_match['score']})"}

    return {
        "success": True,
        "match_type": "fuzzy_name",
        "score": best_match["score"],
        "fc_owner": best_match["fc_owner"]}


def build_resolved_executive_business_owner(source_owner: dict, fc_owner: dict):
    previous_addresses = []
    for previous_address in source_owner.get("previous_addresses") or []:
        previous_addresses.append({
            "house_number_or_name": previous_address.get("address_house_number_or_name"),
            "street": previous_address.get("address_street"),
            "city": previous_address.get("address_city"),
            "postcode": previous_address.get("address_postcode")})

    return {
        "id": fc_owner.get("id"),
        "registry_name": fc_owner.get("registry_name"),
        "first_name": source_owner.get("first_name"),
        "last_name": source_owner.get("last_name"),
        "percent_shares_held": fc_owner.get("percent_shares_held"),
        "fc_person_id": fc_owner.get("fc_person_id"),
        "date_of_birth": source_owner.get("date_of_birth"),
        "address": {
            "house_number_or_name": source_owner.get("address_house_number_or_name"),
            "street": source_owner.get("address_street"),
            "city": source_owner.get("address_city"),
            "postcode": source_owner.get("address_postcode")},
        "previous_addresses": previous_addresses}


def resolve_executive_business_owners_from_next_action(salesforce_payload: dict, next_action_response: dict):
    potential_owners = (next_action_response.get("attributes", {}).get("potential_executive_business_owners") or [])
    majority_owners = extract_majority_executive_business_owners(next_action_response)

    salesforce_payload.setdefault("system", {})
    salesforce_payload["system"]["potential_executive_business_owners"] = potential_owners
    salesforce_payload["system"]["majority_executive_business_owners"] = majority_owners

    source_owners = salesforce_payload.get("executive_business_owners") or []
    fc_owners_to_match = majority_owners if majority_owners else potential_owners

    if not fc_owners_to_match:
        return {
            "success": False,
            "message": "FundingCircle next_action did not return any potential executive business owners.",
            "salesforce_payload": salesforce_payload}

    resolved_owners = []
    unmatched_source_owners = []

    if not source_owners:
        for fc_owner in fc_owners_to_match:
            if not fc_owner.get("fc_person_id") or not fc_owner.get("registry_name"):
                continue

            resolved_owners.append({
                "id": fc_owner.get("id"),
                "registry_name": fc_owner.get("registry_name"),
                "first_name": None,
                "last_name": None,
                "percent_shares_held": fc_owner.get("percent_shares_held"),
                "fc_person_id": fc_owner.get("fc_person_id"),
                "date_of_birth": None,
                "address": {
                    "house_number_or_name": None,
                    "street": None,
                    "city": None,
                    "postcode": None},
                "previous_addresses": []})

        salesforce_payload["resolved_executive_business_owners"] = resolved_owners
        return {
            "success": True,
            "salesforce_payload": salesforce_payload,
            "resolved_executive_business_owners": resolved_owners,
            "unmatched_source_owners": []}

    for source_owner in source_owners:
        match_result = find_best_fc_owner_match(source_owner, fc_owners_to_match, min_score=90)

        if match_result["success"] is False:
            unmatched_source_owners.append({
                "source_owner": source_owner,
                "reason": match_result["message"]})
            continue

        fc_owner = match_result["fc_owner"]
        resolved_owner = build_resolved_executive_business_owner(source_owner, fc_owner)
        resolved_owners.append(resolved_owner)

    salesforce_payload["resolved_executive_business_owners"] = resolved_owners

    if not resolved_owners:
        return {
            "success": False,
            "message": "No executive business owners could be resolved from next_action.",
            "salesforce_payload": salesforce_payload,
            "resolved_executive_business_owners": resolved_owners,
            "unmatched_source_owners": unmatched_source_owners}

    return {
        "success": True,
        "salesforce_payload": salesforce_payload,
        "resolved_executive_business_owners": resolved_owners,
        "unmatched_source_owners": unmatched_source_owners}


def validate_resolved_executive_business_owners(salesforce_payload: dict):
    owners = salesforce_payload.get("resolved_executive_business_owners") or []

    if not owners:
        return {
            "success": False,
            "message": "No resolved executive business owners were found to submit."}

    invalid_owners = []
    for owner in owners:
        if not owner.get("id") and owner.get("id") != 0:
            invalid_owners.append(owner)
            continue
        if not owner.get("fc_person_id") or not owner.get("registry_name"):
            invalid_owners.append(owner)

    if invalid_owners:
        return {
            "success": False,
            "message": f"Some resolved executive business owners are missing id, fc_person_id, or registry_name: {invalid_owners}"}

    return {"success": True}