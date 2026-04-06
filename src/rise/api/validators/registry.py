from fastapi import HTTPException
from rise.api.validators.base import BaseValidator
from rise.api.validators.funding_circle import FundingCircleValidator

# Maps the URL company slug to its validator instance.
# Adding a new lender = add one entry here + create a new validator file.
# Both funding-circle and funding-circle-sandbox route to the same worker and FC environment.
# The -sandbox suffix exists so Salesforce sandbox can use a distinct URL from Salesforce prod.
# mock=true skips the worker (validation only); mock=false submits to FC regardless of caller.
REGISTRY: dict[str, BaseValidator] = {
    "funding-circle": FundingCircleValidator(),
    "funding-circle-sandbox": FundingCircleValidator(),
}


def get_validator(company: str) -> BaseValidator:
    validator = REGISTRY.get(company)
    if not validator:
        raise HTTPException(
            status_code=404,
            detail="Unknown company '%s'. Supported: %s" % (company, list(REGISTRY)))
    return validator
