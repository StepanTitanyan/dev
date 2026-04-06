import re
from typing import Any
from rapidfuzz import fuzz

_GENERIC_COMPANY_TOKENS = {"limited", "company", "liability", "partnership", "llp", "uk", "the"}


def normalize_company_name(name: str) -> str:
    if not name:
        return ""

    name = name.lower().strip()

    replacements = {
        "&": " and ",
        " ltd ": " limited ",
        " ltd. ": " limited ",
        " llp ": " limited liability partnership ",
        " co ": " company ",
        "(uk)": " uk "}

    name = f" {name} "
    for old, new in replacements.items():
        name = name.replace(old, new)

    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def get_company_name(company: dict) -> str:
    return company.get("company_name", "") or ""


def get_company_number(company: dict) -> str:
    return (company.get("company_number") or "").strip().upper()


def exact_company_number_match(companies: list[dict], target_company_number: str) -> dict | None:
    if not target_company_number:
        return None

    target = target_company_number.strip().upper()

    for company in companies:
        if get_company_number(company) == target:
            return company
    return None


def exact_normalized_name_matches(companies: list[dict], target_name: str) -> list[dict]:
    target_normalized = normalize_company_name(target_name)
    if not target_normalized:
        return []

    matches = []
    for company in companies:
        if normalize_company_name(get_company_name(company)) == target_normalized:
            matches.append(company)

    return matches


def score_company_name(target_name: str, company_name: str) -> float:
    target_norm = normalize_company_name(target_name)
    company_norm = normalize_company_name(company_name)

    if not target_norm or not company_norm:
        return 0.0

    ratio_score = fuzz.ratio(target_norm, company_norm)
    partial_score = fuzz.partial_ratio(target_norm, company_norm)
    token_score = fuzz.token_sort_ratio(target_norm, company_norm)
    return (ratio_score * 0.35) + (partial_score * 0.25) + (token_score * 0.40)


def _informative_tokens(name: str) -> set[str]:
    normalized = normalize_company_name(name)
    return {token for token in normalized.split() if len(token) >= 4 and token not in _GENERIC_COMPANY_TOKENS}


def _has_sufficient_token_overlap(target_name: str, company_name: str) -> bool:
    target_tokens = _informative_tokens(target_name)
    company_tokens = _informative_tokens(company_name)

    if not target_tokens:
        return True

    return target_tokens.issubset(company_tokens)


def rank_companies(companies: list[dict], target_name: str) -> list[dict]:
    ranked = []

    for company in companies:
        company_name = get_company_name(company)
        score = score_company_name(target_name, company_name)
        token_overlap_ok = _has_sufficient_token_overlap(target_name, company_name)

        ranked.append({
            "score": score,
            "token_overlap_ok": token_overlap_ok,
            "company": company,
            "company_name": company_name,
            "company_number": company.get("company_number")})
    ranked.sort(key=lambda x: (x["token_overlap_ok"], x["score"]), reverse=True)
    return ranked


def find_best_company_match(search_result: list[dict], salesforce_company_name: str, salesforce_company_number: str | None = None, min_score: float = 88.0) -> dict[str, Any]:
    companies = search_result or []
    if not companies:
        return {
            "success": False,
            "reason": "no_companies_found",
            "best_company": None,
            "score": None,
            "top_candidates": []}

    if salesforce_company_number:
        exact_match = exact_company_number_match(companies, salesforce_company_number)
        if exact_match:
            return {
                "success": True,
                "reason": "exact_company_number_match",
                "best_company": exact_match,
                "score": 100.0,
                "top_candidates": [
                    {
                        "score": 100.0,
                        "company_name": get_company_name(exact_match),
                        "company_number": exact_match.get("company_number")}]}

    exact_name_matches = exact_normalized_name_matches(companies, salesforce_company_name)
    if len(exact_name_matches) == 1:
        exact_name_match = exact_name_matches[0]
        return {
            "success": True,
            "reason": "exact_normalized_name_match",
            "best_company": exact_name_match,
            "score": 100.0,
            "top_candidates": [
                {
                    "score": 100.0,
                    "company_name": get_company_name(exact_name_match),
                    "company_number": exact_name_match.get("company_number")}]}

    if len(exact_name_matches) > 1:
        return {
            "success": False,
            "reason": "multiple_exact_name_matches",
            "best_company": None,
            "score": 100.0,
            "top_candidates": [
                {
                    "score": 100.0,
                    "company_name": get_company_name(company),
                    "company_number": company.get("company_number")} for company in exact_name_matches[:5]]}

    ranked = rank_companies(companies, salesforce_company_name)
    top_candidates = [
        {
            "score": round(item["score"], 2),
            "company_name": item["company_name"],
            "company_number": item["company_number"]} for item in ranked[:5]]

    best = ranked[0]
    if not best["token_overlap_ok"]:
        return {
            "success": False,
            "reason": "insufficient_token_overlap",
            "best_company": None,
            "score": round(best["score"], 2),
            "top_candidates": top_candidates}

    effective_min_score = min_score
    if not salesforce_company_number:
        effective_min_score = 95.0

    if best["score"] < effective_min_score:
        return {
            "success": False,
            "reason": "no_match_above_threshold",
            "best_company": None,
            "score": round(best["score"], 2),
            "top_candidates": top_candidates}

    if len(ranked) > 1 and not salesforce_company_number:
        second_best = ranked[1]
        if (best["score"] - second_best["score"]) < 3.0:
            return {
                "success": False,
                "reason": "ambiguous_fuzzy_match",
                "best_company": None,
                "score": round(best["score"], 2),
                "top_candidates": top_candidates}

    return {
        "success": True,
        "reason": "fuzzy_name_match",
        "best_company": best["company"],
        "score": round(best["score"], 2),
        "top_candidates": top_candidates}