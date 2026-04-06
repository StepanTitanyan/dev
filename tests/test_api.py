"""
API endpoint tests using FastAPI's TestClient with a mocked database.
No real DB connection required — the DB dependency is overridden.
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from rise.api.server import app
from rise.db.session import get_db


VALID_HEADERS = {"x-api-token": "test-token"}

VALID_FC_PAYLOAD = {
    "salesforce_record_id": "a0B5g00000XyZabEAF",
    "commission": 2.5,
    "loan_request": {"requested_amount_gbp": 50000, "term_requested_months": 24},
    "company": {
        "business_structure": "limited",
        "company_number": "12345678",
        "client_email": "finance@acme.co.uk",
    },
    "applicant": {"first_name": "John", "last_name": "Smith", "mobile_number": "07700900000"},
    "loan_purpose": {"loan_purpose": "Working capital"},
    "business_performance": {
        "self_stated_industry": "Technology",
        "full_time_employees": 10,
        "company_established_or_registered_in_northern_ireland": False,
        "self_stated_turnover": 500000,
        "profit_band": 75000,
        "overdraft_facility_exists": False,
    },
}


def _mock_db():
    db = MagicMock()
    db.execute.return_value = None
    return db


@pytest.fixture(autouse=True)
def override_db():
    """Replace the real DB dependency with a mock for every test in this file."""
    app.dependency_overrides[get_db] = _mock_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_api_token(monkeypatch):
    """Make verify_api_token always return True so we can test endpoints freely."""
    monkeypatch.setattr("rise.api.server.verify_api_token", lambda token: True)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def test_missing_token_returns_401(client, monkeypatch):
    monkeypatch.setattr("rise.api.server.verify_api_token", lambda token: False)
    response = client.get("/applications")
    assert response.status_code == 401


def test_invalid_token_returns_401(client, monkeypatch):
    monkeypatch.setattr("rise.api.server.verify_api_token", lambda token: False)
    response = client.get("/applications", headers={"x-api-token": "wrong"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /applications/{company}
# ---------------------------------------------------------------------------

def test_unknown_company_returns_404(client):
    response = client.post("/applications/unknown-lender", json=VALID_FC_PAYLOAD, headers=VALID_HEADERS)
    assert response.status_code == 404


def test_invalid_payload_returns_422(client):
    bad_payload = {"salesforce_record_id": "SF-001"}  # missing required fields
    response = client.post("/applications/funding-circle", json=bad_payload, headers=VALID_HEADERS)
    assert response.status_code == 422


def test_create_application_returns_tracking_id(client):
    mock_app = MagicMock()
    mock_app.tracking_id = "test-tracking-id"
    mock_app.status = "queued"

    with patch("rise.api.controllers.application.get_application_by_salesforce_record_id", return_value=None), \
         patch("rise.api.controllers.application.create_application", return_value=mock_app), \
         patch("rise.api.controllers.application._enqueue"):
        response = client.post("/applications/funding-circle", json=VALID_FC_PAYLOAD, headers=VALID_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["tracking_id"] == "test-tracking-id"
    assert data["status"] == "queued"


def test_duplicate_salesforce_record_returns_existing(client):
    existing = MagicMock()
    existing.tracking_id = "existing-tracking-id"
    existing.status = "completed"

    with patch("rise.api.controllers.application.get_application_by_salesforce_record_id", return_value=existing):
        response = client.post("/applications/funding-circle", json=VALID_FC_PAYLOAD, headers=VALID_HEADERS)

    assert response.status_code == 200
    assert response.json()["tracking_id"] == "existing-tracking-id"
    assert response.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# GET /applications/{tracking_id}
# ---------------------------------------------------------------------------

def test_get_application_not_found_returns_404(client):
    with patch("rise.api.controllers.application.get_application_by_tracking_id", return_value=None):
        response = client.get("/applications/nonexistent-id", headers=VALID_HEADERS)
    assert response.status_code == 404


def test_get_application_returns_status(client):
    from datetime import datetime, timezone
    mock_app = MagicMock()
    mock_app.tracking_id = "abc-123"
    mock_app.salesforce_record_id = "SF-001"
    mock_app.external_id = None
    mock_app.worker_type = "funding_circle"
    mock_app.status = "processing"
    mock_app.current_step = "eligibility_check"
    mock_app.retry_count = 0
    mock_app.last_error = None
    mock_app.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_app.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    mock_app.finished_at = None

    with patch("rise.api.controllers.application.get_application_by_tracking_id", return_value=mock_app):
        response = client.get("/applications/abc-123", headers=VALID_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["tracking_id"] == "abc-123"
    assert data["status"] == "processing"
    assert data["worker_type"] == "funding_circle"


# ---------------------------------------------------------------------------
# POST /applications/{tracking_id}/retry
# ---------------------------------------------------------------------------

def test_retry_non_failed_application_returns_400(client):
    mock_app = MagicMock()
    mock_app.status = "processing"

    with patch("rise.api.controllers.application.get_application_by_tracking_id", return_value=mock_app):
        response = client.post("/applications/abc-123/retry", headers=VALID_HEADERS)

    assert response.status_code == 400
