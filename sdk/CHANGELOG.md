# Changelog

All notable changes to the `reqly` Python SDK.

## 0.1.5 — 2026-10-09

### Fixed
- Batches are now retried on `408`, `429` and any `5xx` response instead of being
  dropped on the first server error. Other `4xx` responses are still dropped immediately.
- Pre-fork servers (gunicorn `--preload`, uWSGI without lazy-apps): forked workers now
  restart the flush thread and open their own HTTP connections. Previously their events
  queued forever and were never shipped.
- Calling `reqly.instrument()` twice on the same app no longer adds a second middleware
  and double-counts every request; the existing client is returned.
- `REQLY_IGNORE_ROUTES` tolerates whitespace and empty entries (`"/health, /metrics"`).

### Changed
- The SDK version is read from package metadata in one place. A source checkout that
  was never installed reports `0.0.0+unknown` instead of a stale hardcoded number.
- Clarified the sampling docs: with `sample_rate < 1.0`, dashboard request counts show
  the sampled volume (the collector does not scale them back up).

## 0.1.4

- Last release before this changelog was started.
