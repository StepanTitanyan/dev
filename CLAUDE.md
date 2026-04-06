# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python FastAPI backend service that automates the FundingCircle broker borrower workflow. It receives Salesforce-shaped application payloads, persists them in PostgreSQL, and orchestrates a long-running state-machine worker that interacts with FundingCircle's APIs.

## Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Run API (development):**
```bash
python -m uvicorn fc_broker.api.server:app --host 0.0.0.0 --port 8000 --reload
```

**Run worker (development):**
```bash
python -m fc_broker.initialize.initialize
```

**Docker (full stack):**
```bash
docker-compose up
```

**Production entrypoint (`start.sh`):**
```bash
# Runs worker in background, FastAPI via Gunicorn
python3 -m fc_broker.initialize.initialize &
gunicorn -k uvicorn.workers.UvicornWorker fc_broker.api.server:app -b 0.0.0.0:8000 --workers 2 --timeout 120
```

**Run tests:**
```bash
python -m pytest tests/
```

**Environment:** Copy `.env.example` to `.env` and populate credentials before running locally. The PYTHONPATH must include `src/`.

## Architecture

### Request-Worker Pattern

The system decouples API ingestion from long-running processing via a database queue or AWS SQS:

1. **API** (`src/fc_broker/api/server.py`) receives a Salesforce-shaped application payload, stores it in PostgreSQL with status `queued`, and optionally enqueues an SQS message.
2. **Worker** (`src/fc_broker/initialize/initialize.py`) runs as a long-lived process, polling DB or SQS for queued applications.
3. **Workflow engine** (`src/fc_broker/workflow/worker.py`) executes the step-based state machine for each application.

### Dual-Mode Queueing

- **Local DB polling** (default, `ENABLE_SQS=false`): worker queries DB for `status='queued'` applications.
- **SQS polling** (`ENABLE_SQS=true`): worker consumes messages from AWS SQS queue; SQS visibility timeout is 15 minutes (covers login + OTP + all FC steps).

### Step-Based State Machine

The workflow in `worker.py` (921 lines) is the core of the service. Steps executed in sequence:

1. `eligibility_check`
2. `get_applicant_details`
3. `get_loan_application_details`
4. `get_company_performance_details`
5. `get_contact_details`
6. `select_executive_business_owners`
7. `submit_bank_statements`
8. `identify_executive_business_owners` (partial success — terminal)
9. `application_submitted` (full success — terminal)

Terminal error states: `company_has_in_flight_app_error`, `user_has_in_flight_loan_application`, `error`, `invalid_application`, `rejected`.

Every step execution is persisted in `application_steps` with request/response JSON and error details for auditability and replay.

### Key Layers

| Layer | Path | Purpose |
|---|---|---|
| API | `src/fc_broker/api/server.py` | FastAPI routes; x-api-token middleware auth |
| Workflow | `src/fc_broker/workflow/worker.py` | Step state machine, retry logic |
| FC Client | `src/fc_broker/clients/client.py` | All FundingCircle HTTP endpoints |
| DB | `src/fc_broker/db/` | SQLAlchemy models, repository pattern |
| Files | `src/fc_broker/files/storage.py` | Local PDF staging; base64 decode + `%PDF` validation |
| Salesforce | `src/fc_broker/salesforce/client.py` | OAuth2 token caching + ContentVersion download |
| OTP | `src/fc_broker/otp/webhook.py` | POST `/sms` Twilio webhook; stores OTP in DB |
| Config | `src/fc_broker/config/config.py` | Env vars + AWS Secrets Manager override |
| Logging | `src/fc_broker/logging_context.py` | contextvars-based log enrichment |

### Database Schema

Tables: `applications`, `application_steps`, `application_events`, `worker_auth_state`, `otp_messages`.

Schema is created via `Base.metadata.create_all(...)` at startup (no Alembic migrations yet — noted as a known gap in README).

### Authentication Flow

The worker session auth is stateful: username/password login → SMS OTP → session stored in `worker_auth_state`. OTP codes arrive via Twilio webhook at POST `/sms`, are persisted in `otp_messages`, and the worker polls the DB waiting for a new unconsumed OTP.

### Error Classification

`FundingCircleIneligibleError` — permanent rejection (no retry). `FundingCircleValidationError` — 422 validation failure. All other failures are retryable with exponential backoff.

### Logging

`logging_context.py` uses Python `contextvars` to inject `tracking_id`, `salesforce_record_id`, and `fc_application_id` into all log lines automatically. Format is CloudWatch-friendly: `[tracking=xxx | sf=yyy | fc=zzz] message`. DEBUG level logs full payloads; suppress DEBUG in production to avoid logging PII.

### External Services

- **FundingCircle API** — main workflow target; broker REST API with username/password + SMS OTP auth
- **AWS SQS** — optional job queue (`queue/sqs.py`)
- **AWS Secrets Manager** — credential injection at runtime (RDS, FC, Salesforce, API token)
- **Salesforce REST API v60.0** — OAuth2 client_credentials; ContentVersion binary file download
- **Twilio** — inbound SMS OTP delivery via webhook

### Deployment

AWS App Runner (`apprunner.yaml`) — Python 3.11, eu-west-2, RDS Aurora PostgreSQL. Production secrets injected from AWS Secrets Manager as environment variables.
