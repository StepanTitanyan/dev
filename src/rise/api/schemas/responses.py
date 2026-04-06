from pydantic import BaseModel, Field


class ApplicationCreateResponse(BaseModel):
    tracking_id: str = Field(description="UUID assigned to track this application")
    status: str = Field(description="Initial status, always 'queued'")


class ApplicationStatusResponse(BaseModel):
    tracking_id: str = Field(description="UUID assigned to track this application")
    salesforce_record_id: str | None = Field(default=None, description="Salesforce record ID from the original payload")
    external_id: str | None = Field(default=None, description="Application ID assigned by the lender (e.g. FundingCircle)")
    worker_type: str | None = Field(default=None, description="Which lender worker processed this (e.g. 'funding_circle')")
    status: str = Field(description="Current status: queued | processing | completed | partially_completed | failed | retrying")
    current_step: str | None = Field(default=None, description="Last workflow step executed")
    retry_count: int = Field(description="Number of retry attempts so far")
    last_error: str | None = Field(default=None, description="Last error message if failed or retrying")
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


class ApplicationListItemResponse(BaseModel):
    id: int
    tracking_id: str
    salesforce_record_id: str | None = None
    external_id: str | None = None
    worker_type: str
    status: str
    current_step: str | None = None
    company_name: str | None = None
    last_error: str | None = None
    retry_count: int
    created_at: str | None = None
    updated_at: str | None = None


class ApplicationListResponse(BaseModel):
    applications: list[ApplicationListItemResponse]
    total: int = Field(description="Total matching records (ignores limit/offset)")
    limit: int
    offset: int


class ApplicationStepResponse(BaseModel):
    id: int
    step_name: str
    step_order: int
    status: str = Field(description="started | succeeded | failed")
    request_json: dict | None = None
    response_json: dict | None = None
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class ApplicationStepsResponse(BaseModel):
    tracking_id: str
    steps: list[ApplicationStepResponse]


class ApplicationEventResponse(BaseModel):
    id: int
    event_type: str
    message: str | None = None
    data: dict | None = None
    created_at: str | None = None


class ApplicationEventsResponse(BaseModel):
    tracking_id: str
    events: list[ApplicationEventResponse]


class WorkerSessionResponse(BaseModel):
    worker_type: str
    status: str = Field(description="logged_out | waiting_for_otp | authenticated")
    is_authenticated: bool
    waiting_for_otp_since: str | None = None
    otp_received_at: str | None = None
    last_login_at: str | None = None
    last_error: str | None = None
    updated_at: str | None = None
