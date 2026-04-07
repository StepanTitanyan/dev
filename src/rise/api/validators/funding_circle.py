from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from rise.api.validators.base import BaseValidator


class LoanRequestSchema(BaseModel):
    requested_amount_gbp: float | int
    term_requested_months: int

    @model_validator(mode="after")
    def validate_loan_request(self):
        if float(self.requested_amount_gbp) <= 0:
            raise ValueError("requested_amount_gbp must be greater than 0")
        if int(self.term_requested_months) <= 0:
            raise ValueError("term_requested_months must be greater than 0")
        return self


class CompanySchema(BaseModel):
    business_structure: str
    company_search_term: str | None = None
    company_name: str | None = None
    company_number: str | None = None
    company_codas_id: str | None = None
    unique_company_identifier: str | None = None
    client_email: str

    @field_validator("business_structure", "client_email")
    @classmethod
    def not_empty(cls, value: str):
        if not value or not value.strip():
            raise ValueError("Field cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def requires_identifier(self):
        if not self.company_search_term and not self.company_name and not self.company_number:
            raise ValueError("At least one of company_search_term, company_name or company_number is required")
        return self


class ApplicantSchema(BaseModel):
    first_name: str
    last_name: str
    mobile_number: str

    @field_validator("first_name", "last_name", "mobile_number")
    @classmethod
    def not_empty(cls, value: str):
        if not value or not value.strip():
            raise ValueError("Field cannot be empty")
        return value.strip()


class LoanPurposeSchema(BaseModel):
    loan_purpose: str
    loan_for_assets: str | None = None
    loan_purpose_details_property: bool = False
    loan_purpose_details_new_sector: bool = False
    loan_purpose_details_personal_debt: bool = False
    loan_purpose_details_outside_uk: bool = False
    loan_purpose_details_not_for_applicant: bool = False

    @field_validator("loan_purpose")
    @classmethod
    def not_empty(cls, value: str):
        if not value or not value.strip():
            raise ValueError("loan_purpose is required")
        return value.strip()

    @model_validator(mode="after")
    def assets_required(self):
        if self.loan_purpose == "Fund vehicle, equipment or machinery" and not self.loan_for_assets:
            raise ValueError("loan_for_assets is required when loan_purpose is 'Fund vehicle, equipment or machinery'")
        return self


class BusinessPerformanceSchema(BaseModel):
    self_stated_industry: str
    full_time_employees: int
    company_established_or_registered_in_northern_ireland: bool
    company_manufactures_or_sells_goods_or_operates_in_northern_ireland_electricity_market: Optional[bool] = None
    self_stated_turnover: float | int
    self_stated_turnover_for_2019: float | int | None = None
    profit_band: int
    overdraft_facility_exists: bool
    overdraft_limit_amount: float | int | None = None
    overdraft_current_usage_amount: float | int | None = None
    new_debt_last_12_months_amount: float | int | None = None
    has_taken_more_than_25000_borrowing_last_12_months: Optional[bool] = None

    @field_validator("self_stated_industry")
    @classmethod
    def not_empty(cls, value: str):
        if not value or not value.strip():
            raise ValueError("self_stated_industry is required")
        return value.strip()

    @model_validator(mode="after")
    def validate_financials(self):
        if int(self.full_time_employees) < 0:
            raise ValueError("full_time_employees cannot be negative")
        if float(self.self_stated_turnover) < 0:
            raise ValueError("self_stated_turnover cannot be negative")
        if self.self_stated_turnover_for_2019 is not None and float(self.self_stated_turnover_for_2019) < 0:
            raise ValueError("self_stated_turnover_for_2019 cannot be negative")
        if self.overdraft_facility_exists:
            if self.overdraft_limit_amount is None:
                raise ValueError("overdraft_limit_amount is required when overdraft_facility_exists is True")
            if self.overdraft_current_usage_amount is None:
                raise ValueError("overdraft_current_usage_amount is required when overdraft_facility_exists is True")
        if self.overdraft_limit_amount is not None and float(self.overdraft_limit_amount) < 0:
            raise ValueError("overdraft_limit_amount cannot be negative")
        if self.overdraft_current_usage_amount is not None and float(self.overdraft_current_usage_amount) < 0:
            raise ValueError("overdraft_current_usage_amount cannot be negative")
        return self


class PreviousAddressSchema(BaseModel):
    address_house_number_or_name: str | None = None
    address_street: str | None = None
    address_city: str | None = None
    address_postcode: str | None = None


class ExecutiveBusinessOwnerSchema(BaseModel):
    registry_name: str
    first_name: str
    last_name: str
    percent_shares_held: float | int
    date_of_birth: str
    address_house_number_or_name: str
    address_street: str
    address_city: str
    address_postcode: str
    previous_addresses: list[PreviousAddressSchema] = Field(default_factory=list)
    fc_person_id: str | None = None


class FundingCirclePayload(BaseModel):
    salesforce_record_id: str
    mock: bool = False
    loan_request: LoanRequestSchema
    commission: float | int
    company: CompanySchema
    applicant: ApplicantSchema
    loan_purpose: LoanPurposeSchema
    business_performance: BusinessPerformanceSchema
    executive_business_owners: list[ExecutiveBusinessOwnerSchema] = Field(default_factory=list)
    content_version_ids: list[str] = Field(default_factory=list)

    @field_validator("salesforce_record_id")
    @classmethod
    def not_empty(cls, value: str):
        if not value or not value.strip():
            raise ValueError("salesforce_record_id is required")
        return value.strip()

    @field_validator("commission")
    @classmethod
    def non_negative(cls, value: float | int):
        if float(value) < 0:
            raise ValueError("commission cannot be negative")
        return value


class FundingCircleValidator(BaseValidator):
    worker_type = "funding_circle"
    queue_url_setting = "SQS_QUEUE_URL"

    def validate(self, raw: dict) -> dict:
        return FundingCirclePayload(**raw).model_dump()


