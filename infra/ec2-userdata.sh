#!/bin/bash
# Amazon Linux 2023 bootstrap — installs Docker + TimescaleDB (the -ha image,
# which bundles the TimescaleDB Toolkit).
# Usage: pass as --user-data when launching an EC2 instance (see infra/DEPLOY.md)
# Replace POSTGRES_PASSWORD with your own secure password before use.
#
# The Reqly schema is NOT applied here: the collector applies
# collector/migrations/*.sql itself on startup, so there is one copy of the
# schema instead of one per deployment path.
set -e

POSTGRES_USER=reqly
POSTGRES_DB=reqly
POSTGRES_PASSWORD=your_password_here   # <-- change this

LOGFILE=/var/log/reqly-setup.log
exec > >(tee -a "$LOGFILE") 2>&1

# This instance exposes Postgres on 5432 (see DEPLOY.md), so refuse to boot
# a database with the placeholder password.
if [ "$POSTGRES_PASSWORD" = "your_password_here" ] || [ -z "$POSTGRES_PASSWORD" ]; then
  echo "ERROR: set POSTGRES_PASSWORD in infra/ec2-userdata.sh before launching." >&2
  exit 1
fi

echo "=== Reqly TimescaleDB Setup ==="
date

# Small instances (t3.micro / t4g.micro have 1 GB RAM): add 1 GB of swap so a
# memory spike (aggregate refresh, big backfill) slows Postgres down instead
# of getting it OOM-killed.
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 2000000 ] && [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "Added 1 GB swap"
fi

# Install Docker
amazon-linux-extras install docker -y 2>/dev/null || dnf install -y docker
systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user
echo "Docker started"

# Create data dir
# The -ha image runs Postgres as uid 1000 with PGDATA under /home/postgres/pgdata.
mkdir -p /data/pgdata
chown -R 1000:1000 /data/pgdata

# Pull + run TimescaleDB
docker pull timescale/timescaledb-ha:pg16

docker run -d \
  --name timescaledb \
  -p 5432:5432 \
  -e POSTGRES_DB=${POSTGRES_DB} \
  -e POSTGRES_USER=${POSTGRES_USER} \
  -e POSTGRES_PASSWORD=${POSTGRES_PASSWORD} \
  -v /data/pgdata:/home/postgres/pgdata \
  --restart unless-stopped \
  timescale/timescaledb-ha:pg16

echo "Container started, waiting for DB to be ready..."

for i in $(seq 1 30); do
  if docker exec timescaledb pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB} 2>/dev/null; then
    echo "DB ready after $((i*5))s"
    break
  fi
  sleep 5
done

echo "=== Reqly setup complete ==="
