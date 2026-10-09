# Reqly — AWS Production Deployment

Full production setup: EC2 (TimescaleDB) + Collector + SAM (Lambda weekly insights).

---

## What gets deployed

```
EC2 (t3.small)                     AWS SAM Stack
──────────────                     ─────────────────────────────
TimescaleDB (Docker)   ◄───────    Lambda (reqly-weekly-insights)
                                   EventBridge (every Sunday 23:00 UTC)
                                   S3 (insight report archive)
```

Your app ships data to the collector, which writes to EC2 TimescaleDB. Lambda reads from the same DB weekly.

---

## Prerequisites

- AWS account with CLI configured (`aws configure`)
- SAM CLI installed (`pip install aws-sam-cli`)
- Groq API key (free at https://console.groq.com/keys) — optional, skip for plain-text insights

---

## Step 1 — Launch EC2 (TimescaleDB)

```bash
# Create security group
aws ec2 create-security-group \
  --group-name reqly-db-sg \
  --description "TimescaleDB for Reqly"

# Get the security group ID from output, then open ports
aws ec2 authorize-security-group-ingress --group-id <sg-id> \
  --protocol tcp --port 5432 --cidr 0.0.0.0/0
# ^ Open to the internet because the Lambda (no VPC) and an off-box collector
#   have no fixed IP. If the collector runs on this instance and you skip the
#   Lambda, restrict this to your own IP instead. Either way, set a strong
#   POSTGRES_PASSWORD in ec2-userdata.sh -- the script refuses the placeholder.

aws ec2 authorize-security-group-ingress --group-id <sg-id> \
  --protocol tcp --port 22 --cidr 0.0.0.0/0

# Launch EC2 (Amazon Linux 2023, ap-south-1)
# User data script auto-installs Docker + TimescaleDB on first boot
# (the collector applies the schema itself when it first starts)
aws ec2 run-instances \
  --image-id ami-0f2f85bcae7ec46bd \
  --instance-type t3.small \
  --key-name <your-key-pair> \
  --security-group-ids <sg-id> \
  --user-data file://infra/ec2-userdata.sh \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=reqly-db}]'
```

Wait ~3 minutes for the instance to boot and run the user data script (installs Docker, pulls TimescaleDB).

Get the public IP:
```bash
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=reqly-db" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text
```

**Note:** Assign an Elastic IP if you want a static address that survives restarts:
```bash
aws ec2 allocate-address
aws ec2 associate-address --instance-id <instance-id> --allocation-id <eip-alloc-id>
```

---

## Step 2 — Deploy the SAM Stack (Lambda insights)

```bash
cd infra/sam
sam build

sam deploy --parameter-overrides \
  "DatabaseUrl=postgresql://reqly:your_password@<ec2-ip>:5432/reqly?sslmode=require" \
  "GroqApiKey=gsk_your_key_here" \
  "Environment=production"
```

SAM creates:
- `reqly-weekly-insights` Lambda function
- EventBridge schedule (Sunday 23:00 UTC)
- S3 bucket for JSON report archive

Because the Lambda now owns the weekly run, start the collector with
`INSIGHTS_SCHEDULER_ENABLED=false` (Step 3) so the report isn't generated twice.

With `Environment=demo` (the default) SAM also creates an **unauthenticated**
Function URL — anyone who has it can trigger a run (Groq calls + DB load).
Use it only for walkthroughs; get the URL from the output:
```bash
curl -X POST <lambda-url>
```

---

## Step 3 — Run the Collector

The collector is a FastAPI app that receives SDK data and serves the dashboard.

**Option A — Docker on EC2 (same instance)**

SSH into EC2:
```bash
ssh -i your-key.pem ec2-user@<ec2-ip>
```

Run collector:
```bash
docker run -d \
  --name reqly-collector \
  --network host \
  -e DATABASE_URL="postgresql://reqly:your_password@localhost:5432/reqly" \
  -e REQLY_INGEST_KEY="your_ingest_key" \
  -e REQLY_READ_KEY="your_read_key" \
  -e GROQ_API_KEY="gsk_your_key_here" \
  -e CORS_ORIGINS="*" \
  -e INSIGHTS_SCHEDULER_ENABLED=false \
  -p 8000:8000 \
  your-registry/reqly-collector:latest
```

**Option B — Separate server / Render / Fly.io**

Set these environment variables wherever you deploy the collector:
```env
DATABASE_URL=postgresql://reqly:your_password@<ec2-ip>:5432/reqly?sslmode=require
REQLY_INGEST_KEY=your_ingest_key
REQLY_READ_KEY=your_read_key
GROQ_API_KEY=gsk_your_key_here
CORS_ORIGINS=*
INSIGHTS_SCHEDULER_ENABLED=false   # the SAM Lambda runs the weekly job
FORWARDED_ALLOW_IPS=*              # only if the collector is reachable solely via the platform's proxy
```

`REQLY_READ_KEY` ends up in the dashboard's JavaScript (Step 4), so it must be
different from `REQLY_INGEST_KEY`.

---

## Step 4 — Run the Dashboard

The dashboard is a static React SPA. Build it:

```bash
cd dashboard
VITE_COLLECTOR_URL=https://your-collector-url \
VITE_READ_KEY=your_read_key \
npm run build
```

Deploy `dist/` to any static host — Vercel, Netlify, S3+CloudFront, or serve via nginx.

---

## Step 5 — Instrument your app

```bash
pip install reqly
```

```python
import reqly
reqly.instrument(app,
    service_name="my-api",
    collector_url="https://your-collector-url",
    api_key="your_ingest_key")
```

---

## Useful commands

```bash
# SSH into EC2
ssh -i your-key.pem ec2-user@<ec2-ip>

# Check TimescaleDB inside EC2
docker exec -it timescaledb psql -U reqly -d reqly

# Trigger insights manually (without waiting for Sunday)
curl -X POST "https://your-collector-url/v1/insights/generate?service_name=my-api" \
  -H "X-Reqly-Key: your_read_key"

# Or via the Lambda URL (Environment=demo stacks only)
curl -X POST <lambda-url>

# Stop EC2 when not in use (saves cost — only EBS charges remain)
aws ec2 stop-instances --instance-ids <instance-id>
aws ec2 start-instances --instance-ids <instance-id>
```

---

## Estimated AWS cost

| Resource | Cost |
|---|---|
| EC2 t3.small | ~$15/month |
| EBS 20GB gp3 | ~$1.60/month |
| Lambda + EventBridge + S3 | $0/month (permanent free tier) |
| **Total** | **~$17/month** |

Stop EC2 when not in use → only ~$1.60/month (EBS only).
