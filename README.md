# FundingCircle Broker Automation

Backend service for automating the FundingCircle broker borrower flow from a Salesforce-shaped payload.

This project accepts application payloads through a FastAPI API, stores them in PostgreSQL, optionally enqueues them to AWS SQS, and processes them through a stateful worker that logs into FundingCircle, waits for OTP, bootstraps the broker session, and runs the borrower workflow step by step.

It also supports the bank-statement upload branch, where PDF files are first staged locally and then uploaded to FundingCircle through a presigned upload flow when required.

---

## What the project does

The system currently supports:

- receiving borrower applications through an API
- persisting application state in PostgreSQL
- persisting step-by-step workflow history
- persisting worker auth state and OTP messages
- optional SQS-based queueing
- long-running worker orchestration
- FundingCircle login, MFA, and session bootstrap
- borrower workflow progression through FundingCircle action steps
- company matching
- executive business owner matching
- local bank-statement PDF staging
- FundingCircle document upload through presigned URLs
- CloudWatch-friendly application logging through Python `logging`

---

## Current architecture

The project is organized into these main parts:

### API
`src/fc_broker/api/server.py`

The FastAPI app:

- receives Salesforce-shaped application payloads
- creates application records
- saves staged PDF documents if included
- enqueues application jobs to SQS when enabled
- exposes application status and step history endpoints
- exposes worker auth status
- mounts the OTP webhook router

### Database
`src/fc_broker/db/`

Contains:

- `models.py`
- `repositories.py`
- `session.py`

The main tables are:

- `applications`
- `application_steps`
- `worker_auth_state`
- `otp_messages`

### FundingCircle client
`src/fc_broker/clients/client.py`

Contains the HTTP request layer for:

- login
- OTP submit
- OAuth/session bootstrap
- broker validation
- company search
- eligibility checks
- applicant details
- loan application details
- company performance details
- contact details
- executive business owners
- next action polling
- presigned upload URL retrieval
- raw file upload to storage
- FundingCircle document creation
- bank statement action submission

### Workflow engine
`src/fc_broker/workflow/worker.py`

Implements the step-based state machine and persisted step execution.

### Worker runner
`src/fc_broker/initialize/initialize.py`

Runs the long-lived worker loop in one of two modes:

- local DB polling mode
- SQS polling mode

It also handles session authentication and OTP waiting.

### File staging
`src/fc_broker/files/storage.py`

Responsible for:

- creating local processing directories
- decoding and saving base64 PDFs
- validating PDF structure
- listing files for later upload
- renaming processing folders from tracking id to FundingCircle application id

### Queue
`src/fc_broker/queue/sqs.py`

Contains the SQS enqueue helper used by the API.

### Matching
`src/fc_broker/matching/`

Contains:

- company matching
- owner matching

### OTP webhook
`src/fc_broker/otp/webhook.py`

Accepts OTP messages and persists them for the worker to consume.

---

## Workflow steps currently supported

The worker currently supports the following flow branches:

- `eligibility_check`
- `get_applicant_details`
- `get_loan_application_details`
- `get_company_performance_details`
- `get_contact_details`
- `select_executive_business_owners`
- `submit_bank_statements`
- `identify_executive_business_owners`
- `application_submitted`

Important behavior:

- `identify_executive_business_owners` is treated as a partial-success terminal state
- `application_submitted` is treated as a full-success terminal state
- `awaiting_next_action` is handled through polling
- retryable failures are pushed back with delay
- terminal failures are persisted to the application record

---

## Bank statement upload flow

Some applications require a bank-statement upload step after the shareholder page.

The implemented upload flow is:

1. PDF files are received by the API and stored locally in a processing folder
2. the worker reaches the `submit_bank_statements` step
3. it asks FundingCircle for a presigned upload URL
4. it uploads the raw PDF bytes to the storage URL
5. it creates the FundingCircle document metadata record
6. it submits the bank-statement action
7. it polls until the next state appears

Important note:

The PDF bytes are not sent inside the JSON action payload.  
The file is uploaded separately to the presigned storage URL, and then the metadata is registered with FundingCircle.

---

## Logging and CloudWatch

The project now uses Python `logging` instead of relying on raw `print()` calls for operational visibility.

This is the intended CloudWatch-friendly behavior:

- logs are emitted to stdout
- AWS can collect stdout/stderr into CloudWatch Logs
- logs are written with levels such as `INFO`, `WARNING`, and `ERROR`

Examples of events that are logged:

- API application creation
- SQS enqueue attempt and result
- worker startup mode
- SQS polling activity
- OTP wait/auth events
- workflow step start and finish
- retries and failures


---

## SQS behavior

The project supports optional SQS queueing.

### API side
When an application is created:

- it is always saved in the database
- if SQS is enabled and a queue URL is configured, the API sends a message to SQS
- if SQS is disabled, the API logs that enqueue is skipped/disabled

### Worker side
When the worker runs in SQS mode:

- it polls SQS
- it loads the application id from the message body
- it processes the application
- it deletes the SQS message only after successful processing of that message cycle

### Common reasons SQS appears to do nothing

Usually one of these:

- `ENABLE_SQS=false`
- missing `SQS_QUEUE_URL`
- missing AWS credentials
- API and worker are running with different environment values
- the worker is running in local polling mode instead of SQS mode

---

## Database entities

### applications
Stores:

- internal id
- tracking id
- salesforce record id
- external FundingCircle application id
- status
- current step
- raw input payload
- working payload
- retry counters
- timestamps

### application_steps
Stores per-step execution history:

- application id
- step name
- step order
- status
- request JSON
- response JSON
- error message
- start/end timestamps

### worker_auth_state
Stores worker authentication state such as:

- waiting for OTP
- authenticated
- latest auth session token
- timestamps
- last error

### otp_messages
Stores incoming OTP messages and whether they were consumed.

---

## Local running

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set environment variables

Example `.env`:

```env
APP_USERNAME=your_fundingcircle_username
APP_PASSWORD=your_fundingcircle_password

OTP_WAIT_SECONDS=120

APP_HOST=0.0.0.0
APP_PORT=8000

POSTGRES_DB=fc_broker
POSTGRES_USER=fc_user
POSTGRES_PASSWORD=fc_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

AWS_REGION=eu-west-1
SQS_QUEUE_URL=
ENABLE_SQS=false

LOG_LEVEL=INFO
```

If Postgres is running in Docker and exposed to your machine, `POSTGRES_HOST=localhost` is usually correct for local Python processes.

If your code runs inside Docker Compose and Postgres is another service there, `POSTGRES_HOST=db` is usually correct.

### 3. Run the API

```bash
python -m uvicorn fc_broker.api.server:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Run the worker

```bash
python -m fc_broker.initialize.initialize
```

If `ENABLE_SQS=false`, it runs in local DB polling mode.  
If `ENABLE_SQS=true`, it runs in SQS polling mode.

---

## Docker notes

The project currently has Docker support for the API and database side, but the worker deployment path may still need to be wired depending on your setup.

Important:

- the API and worker must use the same environment values for SQS
- the worker must have access to AWS credentials if SQS is enabled
- if using CloudWatch in AWS, logs should be collected from container stdout/stderr

---

## How to verify SQS is working

These are the key logs to look for.

### API
After creating an application:

- `[API] Received application ...`
- `[API] Created application ...`

If SQS is enabled:

- `[QUEUE] Enqueueing application_id=...`
- `[QUEUE] Enqueued application_id=..., message_id=...`

If SQS is disabled:

- `[QUEUE:DISABLED] Would enqueue ...`

### Worker
If running in SQS mode:

- `[INITIALIZE] Starting SQS poll loop`
- `[INITIALIZE] Polled SQS for messages`
- `[INITIALIZE] Received SQS message for application_id=...`
- `[INITIALIZE] Deleted SQS message for application_id=...`

These logs tell you whether the problem is on:

- the API send side
- AWS/SQS config
- the worker receive side
- or workflow processing

---

## Known rough edges / next improvements

The current codebase is strong, but there are still a few areas to improve.

### 1. File typing in local staging
The current local file listing is mostly folder-based. If multiple PDF document types are stored together later, you may want a stronger manifest or DB-backed file record.

### 2. Server file size / responsibility
`server.py` is becoming large and may eventually need splitting into:

- schemas
- routes
- service layer

### 3. Real tests
Some test scripts are currently more like manual integration runners than reproducible unit tests.

### 4. DB migrations
Right now table creation is handled through `Base.metadata.create_all(...)`. Later, Alembic would be a better schema migration strategy.

### 5. Full worker containerization
If you want AWS-style deployment, the worker should be deployed as a proper long-running service with the same env and logging conventions as the API.


