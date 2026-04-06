# ECS Migration Plan — Rise Backend Services
## Account: 667659727588 | Region: eu-west-2 (London)

---

## Current State Snapshot

| Resource | Detail |
|---|---|
| VPC | `vpc-0c40ee9073453d955` — 172.31.0.0/16 (default VPC) |
| Private subnets | **1 only** — `subnet-0c814f338ae27156b` eu-west-2a (172.31.96.0/20) |
| Public subnets | 3 — eu-west-2a/b/c |
| NAT Gateway | `nat-0e60439da503ac325` — 18.135.42.78 (eu-west-2a) |
| RDS | `database-1` — PostgreSQL db.t4g.micro, `database-1.cx68ouakcz0x.eu-west-2.rds.amazonaws.com:5432` |
| SQS | `apprunner-queue` — `https://sqs.eu-west-2.amazonaws.com/667659727588/apprunner-queue` |
| Secrets Manager | `rds!db-*`, `FC_USER_PASS`, `API_BASE_TOKEN`, `Salesforce_Dev_credentials` |
| IAM Role | `apprunner-role` — SQS full access + Secrets Manager read + SSM read |
| App Runner | `raisfunding-app-submission` — runs `start.sh` (both API + background worker) |
| App Runner config | `apprunner.yaml` in repo root (`ConfigurationSource: REPOSITORY`) |
| ECR | Empty — no repositories yet |
| ECS | No clusters |
| ACM | No certificates |

### What `apprunner.yaml` tells us
- **Runtime:** Python 3.11, deps installed to `/app/dependencies`
- **Start:** `sh start.sh` → runs `fc_broker.initialize.initialize` in background, then gunicorn
- **Secrets:** All credentials pulled from Secrets Manager with JSON key extraction (no plaintext)
  - `POSTGRES_PASSWORD` → `rds!db-*:password::`
  - `APP_USERNAME` → `FC_USER_PASS:FC_APP_USERNAME::`
  - `APP_PASSWORD` → `FC_USER_PASS:FC_APP_PASSWORD::`
  - `API_BASE_TOKEN` → `API_BASE_TOKEN:API_BASE_TOKEN::`
  - `SALESFORCE_CLIENT_ID` → `Salesforce_Dev_credentials:SALESFORCE_CLIENT_ID::`
  - `SALESFORCE_CLIENT_SECRET` → `Salesforce_Dev_credentials:SALESFORCE_CLIENT_SECRET::`

### Only real bug to fix during migration
- [ ] SQS visibility timeout is **30s** — must be **900s** (15 min). Worker needs 15 min to complete login + OTP + all FC steps before message becomes re-visible.

---

## Target Architecture

```
Internet (Salesforce IPs + allowed user IPs)
          │
          ▼  HTTPS 443
   ┌─────────────┐
   │  Public ALB │  sg-alb — inbound 443 from Salesforce CIDRs + allowed IPs only
   │  eu-west-2  │  in public subnets: a / b / c
   └──────┬──────┘
          │ port 8000
          ▼
   ┌──────────────────┐
   │  ECS API Service │  sg-ecs-api — inbound 8000 from sg-alb only
   │  Fargate         │  private subnets: a / b / c
   │  FastAPI/gunicorn│  desired: 1–2 tasks, auto-scales
   └──────┬───────────┘
          │
          ├─────────────────────────────────────────┐
          ▼                                         ▼
   ┌──────────────┐                    ┌────────────────────────┐
   │     RDS      │                    │  ECS Worker Service    │
   │  PostgreSQL  │◄───────────────────│  Fargate               │
   │  port 5432   │                    │  fc_broker.initialize  │
   └──────────────┘                    │  desired: 1+ tasks     │
          ▲                            │  sg-ecs-worker         │
          │                            └────────────┬───────────┘
          │                                         │
          │                            ┌────────────▼───────────┐
          │                            │    SQS apprunner-queue │
          │                            │    (reused, fix 900s)  │
          │                            └────────────────────────┘
          │                                         │ outbound via NAT GW
          │                                         ▼
          │                            FundingCircle APIs / Salesforce / Twilio
          │
   sg-rds: allow 5432 from sg-ecs-api + sg-ecs-worker
```

**Access model:** Public ALB with security-group IP allowlist — Salesforce published CIDRs + your users' IPs/VPN. No WAF required initially.

---

## Phase 1 — Network: Add Private Subnets

Currently only eu-west-2a has a private subnet. ALB requires 2+ AZs; ECS tasks should be multi-AZ.

**Create:**
| Subnet | AZ | CIDR | Route Table |
|---|---|---|---|
| `private-ecs-b` | eu-west-2b | 172.31.112.0/20 | New RTB → NAT GW |
| `private-ecs-c` | eu-west-2c | 172.31.128.0/20 | New RTB → NAT GW |

Route tables for both new private subnets: `0.0.0.0/0 → nat-0e60439da503ac325` (existing NAT GW).

> NAT GW sits in eu-west-2a. Cross-AZ NAT traffic has a small per-GB cost. For production hardening, add NAT GWs in eu-west-2b/c. Single NAT GW is acceptable for now.

---

## Phase 2 — ECR Repository

Create one repository: `rise-backend-services`

The same Docker image serves both API and Worker — the entrypoint command differs per ECS task definition.

```
667659727588.dkr.ecr.eu-west-2.amazonaws.com/rise-backend-services:latest
```

Lifecycle policy: keep last 10 images.

**Note on PYTHONPATH:** App Runner installs deps to `/app/dependencies` and sets `PYTHONPATH=/app/dependencies:/app/src`. In Docker, `pip install` goes to site-packages automatically, so ECS task definitions only need `PYTHONPATH=/app/src`.

---

## Phase 3 — IAM Roles

### ECS Task Execution Role (`ecs-task-execution-role`)
Used by the ECS agent to pull images and inject secrets. Trust: `ecs-tasks.amazonaws.com`

Policies:
- `AmazonECSTaskExecutionRolePolicy` (managed) — ECR pull + CloudWatch logs
- Inline: `secretsmanager:GetSecretValue` on the 4 existing secret ARNs

### ECS Task Role (`ecs-task-role`)
Used by the running application code. Trust: `ecs-tasks.amazonaws.com`

Policies (port from `apprunner-role`):
- `AmazonSQSFullAccess`
- `SecretsManagerReadWrite`
- `AmazonSSMReadOnlyAccess`

---

## Phase 4 — Security Groups

| SG Name | Purpose | Inbound | Outbound |
|---|---|---|---|
| `sg-alb` | ALB | 443 from Salesforce CIDRs + allowed IPs | All (to ECS) |
| `sg-ecs-api` | API Fargate tasks | 8000 from `sg-alb` | All (RDS, SQS, internet) |
| `sg-ecs-worker` | Worker Fargate tasks | None | All (RDS, SQS, internet) |
| `sg-rds` (new) | RDS PostgreSQL | 5432 from `sg-ecs-api` + `sg-ecs-worker` | None |

**Salesforce IP ranges:** Retrieve from your org: Setup → Network Access. Also check Salesforce's published EU CIDRs for outbound calls. Add these to `sg-alb`.

**Twilio:** POST `/sms` (OTP webhook) must be reachable. Add Twilio's published IP ranges to `sg-alb` on port 443.

Update RDS to use `sg-rds` instead of the current default SG (`sg-0db6d5e41ac92d656` — currently allows all self-traffic, too broad).

---

## Phase 5 — Application Load Balancer

- **Type:** Application (ALB)
- **Scheme:** internet-facing (Salesforce + users need to reach it from outside VPC)
- **Subnets:** 3 public subnets (eu-west-2a/b/c)
- **SG:** `sg-alb`
- **Listeners:**
  - HTTP 80 → redirect to HTTPS 443
  - HTTPS 443 → forward to target group `tg-api` (requires ACM cert — Phase 11)
- **Target group `tg-api`:**
  - Protocol: HTTP, Port: 8000
  - Target type: IP (required for Fargate)
  - Health check: `GET /` (confirm a 200 endpoint exists, or add `/health`)
  - Healthy threshold: 2, Unhealthy: 3, Interval: 30s

---

## Phase 6 — ECS Cluster

- **Name:** `rise-backend-cluster`
- **Type:** Fargate only (no EC2 to manage)
- **CloudWatch Container Insights:** enabled

---

## Phase 7 — Task Definitions

### API Task Definition (`rise-api`)
| Setting | Value |
|---|---|
| Launch type | FARGATE |
| CPU | 1024 (1 vCPU) |
| Memory | 2048 MB |
| Image | `667659727588.dkr.ecr.eu-west-2.amazonaws.com/rise-backend-services:latest` |
| Command | `gunicorn -k uvicorn.workers.UvicornWorker fc_broker.api.server:app -b 0.0.0.0:8000 --workers 2 --timeout 120` |
| Port | 8000 |
| Log group | `/ecs/rise-api` |

### Worker Task Definition (`rise-worker`)
| Setting | Value |
|---|---|
| Launch type | FARGATE |
| CPU | 512 (0.5 vCPU) |
| Memory | 1024 MB |
| Image | Same image as API |
| Command | `python3 -m fc_broker.initialize.initialize` |
| Port | None (no inbound traffic) |
| Log group | `/ecs/rise-worker` |

**Environment variables — exact mapping from `apprunner.yaml`:**

| Variable | Type | Value / Secret ARN + key |
|---|---|---|
| `POSTGRES_HOST` | plain | `database-1.cx68ouakcz0x.eu-west-2.rds.amazonaws.com` |
| `POSTGRES_PORT` | plain | `5432` |
| `POSTGRES_DB` | plain | `postgres` |
| `POSTGRES_USER` | plain | `postgres` |
| `SQS_QUEUE_URL` | plain | `https://sqs.eu-west-2.amazonaws.com/667659727588/apprunner-queue` |
| `ENABLE_SQS` | plain | `true` |
| `PYTHONPATH` | plain | `/app/src` |
| `SALESFORCE_INSTANCE_URL` | plain | `https://willemrisefunding--risefundev.sandbox.my.salesforce.com` |
| `SALESFORCE_API_VERSION` | plain | `v60.0` |
| `POSTGRES_PASSWORD` | secret | `rds!db-fe918ac3-c452-4cfc-93e7-ce45f0bc42fe-Gcm1Ha` → key `password` |
| `APP_USERNAME` | secret | `FC_USER_PASS-S6WLtK` → key `FC_APP_USERNAME` |
| `APP_PASSWORD` | secret | `FC_USER_PASS-S6WLtK` → key `FC_APP_PASSWORD` |
| `API_BASE_TOKEN` | secret | `API_BASE_TOKEN-Zq6CKl` → key `API_BASE_TOKEN` |
| `SALESFORCE_CLIENT_ID` | secret | `Salesforce_Dev_credentials-uJL0WR` → key `SALESFORCE_CLIENT_ID` |
| `SALESFORCE_CLIENT_SECRET` | secret | `Salesforce_Dev_credentials-uJL0WR` → key `SALESFORCE_CLIENT_SECRET` |

---

## Phase 8 — ECS Services

### API Service (`rise-api-service`)
| Setting | Value |
|---|---|
| Cluster | `rise-backend-cluster` |
| Task definition | `rise-api` |
| Launch type | FARGATE |
| Desired count | 1 (scale to 2 under load) |
| Subnets | private-ecs-a, private-ecs-b, private-ecs-c |
| SG | `sg-ecs-api` |
| Load balancer | `tg-api` on ALB |
| Auto-scaling | CPU > 70% → scale out; CPU < 30% → scale in |

### Worker Service (`rise-worker-service`)
| Setting | Value |
|---|---|
| Cluster | `rise-backend-cluster` |
| Task definition | `rise-worker` |
| Launch type | FARGATE |
| Desired count | 1 |
| Subnets | private-ecs-a, private-ecs-b, private-ecs-c |
| SG | `sg-ecs-worker` |
| Load balancer | None |
| Scaling | Manual for now; add more worker services as new submission targets are added |

---

## Phase 9 — Fix SQS Visibility Timeout

Change `apprunner-queue` visibility timeout from **30s → 900s** (15 minutes).

```bash
aws sqs set-queue-attributes \
  --region eu-west-2 \
  --queue-url "https://sqs.eu-west-2.amazonaws.com/667659727588/apprunner-queue" \
  --attributes VisibilityTimeout=900
```

---

## Phase 10 — CI/CD via Bitbucket Pipelines

Replace App Runner auto-deploy (`AutoDeploymentsEnabled: true`) with `bitbucket-pipelines.yml`:

On push to `main`:
1. `docker build` the image
2. `docker push` to ECR (`667659727588.dkr.ecr.eu-west-2.amazonaws.com/rise-backend-services:latest`)
3. `aws ecs update-service --force-new-deployment` for `rise-api-service`
4. `aws ecs update-service --force-new-deployment` for `rise-worker-service`

IAM policy for the Bitbucket pipeline IAM user:
- `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`
- `ecs:UpdateService`, `ecs:DescribeServices`
- `iam:PassRole` scoped to task roles

---

## Phase 11 — Domain & HTTPS (ACM)

1. Request ACM certificate in eu-west-2 for your API domain (DNS validation)
2. Add CNAME in your DNS pointing to the ALB DNS name
3. Attach cert to ALB HTTPS listener

Until cert is ready, ALB can run HTTP on port 80 for initial testing.

---

## Execution Order

```
Phase 9  Fix SQS visibility timeout         ← do first, safe change, no downtime
Phase 1  Network (add 2 private subnets + route tables)
Phase 2  ECR repository + first Docker build/push
Phase 3  IAM roles (execution + task)
Phase 4  Security groups
Phase 5  ALB + target group
Phase 6  ECS cluster
Phase 7  Task definitions
Phase 8  ECS services
Phase 10 Bitbucket CI/CD pipeline
Phase 11 Domain + ACM (can run in parallel from Phase 5)
         Validate → decommission App Runner
```

---

## Cost Estimate (rough)

| Resource | Current (App Runner) | ECS Fargate |
|---|---|---|
| API compute | ~$25/mo (1vCPU/2GB) | ~$25/mo (1 task 1vCPU/2GB) |
| Worker compute | ~$10/mo (runs inside App Runner via start.sh) | ~$10/mo (0.5vCPU/1GB separate service) |
| NAT Gateway | ~$35/mo (existing) | Same |
| ALB | Not used | ~$20/mo |
| RDS | Same | Same |
| ECR | Free tier | ~$1/mo |
| **Total** | **~$70/mo** | **~$91/mo** |

Main net addition: ALB (~$20/mo). Worker cost is similar since it already runs in App Runner.

---

## Open Questions Before Execution

1. **Domain name** — do you have one already? Required for HTTPS/ACM.
2. **Salesforce outbound IPs** — sandbox org `willemrisefunding--risefundev`. Get these from Salesforce Setup → Network Access to add to `sg-alb`.
3. **Twilio webhook IPs** — Twilio publishes its IP ranges; confirm they should be in `sg-alb` on 443.
4. **Additional workers** — when you add workers for other submission targets, will each be a separate ECS service (recommended — independent scaling and deployment) or multiple tasks in one service?
5. **Health check endpoint** — does the API expose `GET /health` returning 200, or should the ALB use `GET /`?
