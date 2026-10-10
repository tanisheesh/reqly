from app.ask.verify import unverified_numbers

# Real tool results from an eval run (flask-demo, POST /orders after v2).
STEPS = [
    {"tool": "get_stats", "result": {
        "range": ["2026-10-09T03:23Z", "2026-10-10T03:23Z"], "route": "/orders",
        "stats": {"requests": 699, "errors": 234, "error_rate": 0.3348, "p50_ms": 269.1,
                  "p95_ms": 5405.0, "p99_ms": 16240.0}}},
    {"tool": "compare_periods", "result": {
        "before": {"requests": 720, "errors": 19, "error_rate": 0.02639, "p95_ms": 1711.0},
        "after": {"requests": 1102, "errors": 374, "error_rate": 0.3394, "p95_ms": 4606.0},
        "change": {"error_rate": {"delta": 0.313, "ratio": 12.86}, "p95_ms": {"delta": 2895, "ratio": 2.692}}}},
]


def test_numbers_from_the_results_pass_in_any_written_form():
    answer = (
        "In the last 24 h /orders had 699 requests and 234 errors, an error rate of 33.5 % "
        "(p95 ≈ 5.4 s, p99 16,240 ms). Comparing 2026-10-07 14:00 → 2026-10-08 14:00 with the "
        "time since v2, the error rate went from 2.6% to 33.9% (Δ 0.313, ×12.9); p95 1 711 ms → 4 606 ms "
        "(+169 %)."
    )
    assert unverified_numbers(answer, STEPS) == []


def test_mis_copied_numbers_are_flagged():
    answer = "Since v2, /orders had 4 778 errors out of 1,102 requests (error rate 43.4%) on pod-3."
    assert unverified_numbers(answer, STEPS) == ["4 778", "43.4"]


def test_identifiers_dates_and_small_counts_are_ignored():
    answer = "Over the last 7 days on v2 (release 2026.10.0), pod-3 and /v1/orders p95 were fine, top 3 routes."
    assert unverified_numbers(answer, STEPS) == []


def test_signs_strings_and_context_count_as_known():
    steps = [{"tool": "get_slos", "result": {"budget_remaining": -0.0485, "change": {"delta": -5e-05}}},
             {"tool": "get_breakdown", "result": {"values": [{"value": "503", "requests": 11}]}}]
    answer = "Budget left is -4.85% (change -0.00005); 503 (11 req). Data only goes back 90 days, not to March 2025."
    assert unverified_numbers(answer, steps) == ["90", "2025"]
    assert unverified_numbers(answer, steps, context="retention: 90 days. Question: error rates in March 2025?") == []


def test_no_tools_means_every_number_is_unverified():
    assert unverified_numbers("The error rate was 12.5% across 900 requests.", []) == ["12.5", "900"]
