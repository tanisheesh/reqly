# Reqly all-in-one: the collector serving the dashboard on one port.
# Build from the repository root:
#   docker build -f deploy/all-in-one/Dockerfile -t reqly .

FROM node:22-alpine AS dashboard
WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
# The collector writes /config.js at run time (this origin, and the read key
# when the dashboard is public), so nothing is baked into the bundle.
ENV VITE_COLLECTOR_URL="" VITE_READ_KEY=""
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv

COPY collector/requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY collector/pyproject.toml ./
COPY collector/app ./app
COPY collector/migrations ./migrations
RUN pip install --no-cache-dir --no-deps . && useradd --system --uid 1000 --no-create-home reqly

COPY --from=dashboard /app/dist ./dashboard
ENV DASHBOARD_DIR=/srv/dashboard

# Not root: uid 1000 matches the Helm chart's runAsUser.
USER 1000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
