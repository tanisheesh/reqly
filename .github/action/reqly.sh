#!/usr/bin/env bash
# The Reqly GitHub Action (action.yml at the repository root). Settings come
# from environment variables only, so no input is ever spliced into a command.
set -euo pipefail

: "${REQLY_COLLECTOR_URL:?collector-url is required}"
: "${REQLY_API_KEY:?api-key is required}"
: "${REQLY_SERVICE:?service is required}"
url="${REQLY_COLLECTOR_URL%/}"

json_string() {  # a JSON string literal for $1
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1" 2>/dev/null \
    || printf '"%s"' "$(printf '%s' "$1" | sed 's/\/\\/g; s/"/\\"/g')"
}

call() {  # method path [curl args...]: fails the step on any HTTP error, showing the collector's reply
  local method="$1" path="$2"; shift 2
  local out status
  out="$(mktemp)"
  status="$(curl -sS -o "$out" -w '%{http_code}' -X "$method" "$url$path" -H "X-Reqly-Key: $REQLY_API_KEY" "$@")"
  if [ "$status" -ge 400 ]; then
    echo "::error::Reqly $method $path failed with $status: $(cat "$out")"
    exit 1
  fi
}

if [ "${REQLY_DEPLOY_MARKER:-true}" != "false" ]; then
  : "${REQLY_RELEASE:?release is required for a deploy marker}"
  body="{\"service_name\": $(json_string "$REQLY_SERVICE"), \"release\": $(json_string "$REQLY_RELEASE")"
  if [ -n "${REQLY_ENVIRONMENT:-}" ]; then body="$body, \"environment\": $(json_string "$REQLY_ENVIRONMENT")"; fi
  call POST /v1/deployments -H "Content-Type: application/json" --data-binary "$body}"
  echo "Reqly: marked $REQLY_SERVICE release $REQLY_RELEASE${REQLY_ENVIRONMENT:+ in $REQLY_ENVIRONMENT}"
fi

if [ -n "${REQLY_OPENAPI_SPEC:-}" ]; then
  [ -f "$REQLY_OPENAPI_SPEC" ] || { echo "::error::openapi-spec $REQLY_OPENAPI_SPEC not found"; exit 1; }
  case "$REQLY_OPENAPI_SPEC" in
    *.yaml|*.yml) type="application/yaml" ;;
    *) type="application/json" ;;
  esac
  service_path="$(printf '%s' "$REQLY_SERVICE" | sed 's/%/%25/g; s/ /%20/g; s#/#%2F#g; s/?/%3F/g; s/#/%23/g')"
  query=""
  if [ -n "${REQLY_BASE_PATH:-}" ]; then query="?base_path=$(printf '%s' "$REQLY_BASE_PATH" | sed 's/%/%25/g; s#/#%2F#g; s/ /%20/g')"; fi
  call PUT "/v1/services/$service_path/openapi$query" -H "Content-Type: $type" --data-binary "@$REQLY_OPENAPI_SPEC"
  echo "Reqly: uploaded $REQLY_OPENAPI_SPEC for $REQLY_SERVICE"
fi
