FROM python:3.12-slim

WORKDIR /srv

# Dependencies first, from the hash-pinned lock (requirements.txt), so the
# layer is cached until the lock changes and every build gets the same
# packages; then the collector itself, without re-resolving.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations

RUN pip install --no-cache-dir --no-deps . && useradd --system --uid 1000 --no-create-home reqly

# Not root: uid 1000 matches the Helm chart's runAsUser.
USER 1000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
