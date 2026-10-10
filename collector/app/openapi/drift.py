"""OpenAPI drift: the documented API (a spec) vs the API that is actually
called (observed traffic). Pure functions; the queries are in store.py.

- undocumented: operations that get traffic but aren't in the spec
- dead: operations in the spec without traffic in the window
- deprecated_in_use: operations marked deprecated that still get traffic

Paths are compared by shape, so every framework's parameter syntax matches:
/users/{user_id}, /users/{id}, /users/:id and /users/<int:id> are all
/users/{}. An observed route also matches a spec path where the spec has a
parameter and the route a literal (an un-templated /users/42 from an OTLP
exporter matches /users/{}), but a literal spec segment wins over a
parameter: /users/me is matched to /users/me, not /users/{id}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HTTP_METHODS = ("GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE")
# Served by frameworks and proxies without being in the spec; not drift.
IMPLICIT_METHODS = ("HEAD", "OPTIONS")
UNMATCHED_ROUTE = "__unmatched__"
PARAM = "{}"

# {name}, {name:path}, :name, <name>, <int:name>
_PARAM_SEGMENT = re.compile(r"^(\{[^}]*\}|:[A-Za-z_][\w-]*|<[^>]+>)$")


def normalize_path(path: str) -> str:
    """Path shape with every parameter segment as "{}" and no trailing slash."""
    segments = [s for s in path.strip().split("/") if s]
    return "/" + "/".join(PARAM if _PARAM_SEGMENT.match(s) else s for s in segments)


def join_base_path(base_path: str, path: str) -> str:
    base = "/" + base_path.strip("/") if base_path.strip("/") else ""
    return base + ("/" + path.lstrip("/"))


class SpecError(ValueError):
    """The uploaded document isn't an OpenAPI/Swagger spec we can read."""


@dataclass
class Operation:
    method: str
    path: str  # as written in the spec, with the base path
    shape: str
    operation_id: str | None = None
    summary: str | None = None
    deprecated: bool = False

    def describe(self) -> dict:
        out = {"method": self.method, "path": self.path}
        if self.operation_id:
            out["operation_id"] = self.operation_id
        if self.summary:
            out["summary"] = self.summary
        if self.deprecated:
            out["deprecated"] = True
        return out


def spec_base_path(spec: dict) -> str:
    """Swagger 2.0's basePath; OpenAPI 3 servers are hosts, not app paths,
    so their prefix is only used when given explicitly."""
    base = spec.get("basePath")
    return base if isinstance(base, str) else ""


def validate_spec(spec) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("the spec must be a JSON or YAML object")
    if "openapi" not in spec and "swagger" not in spec:
        raise SpecError('not an OpenAPI document: no "openapi" or "swagger" field')
    if not isinstance(spec.get("paths"), dict):
        raise SpecError('the spec has no "paths" object')
    return spec


def spec_operations(spec: dict, base_path: str = "") -> list[Operation]:
    operations = []
    for path, item in spec["paths"].items():
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        full = join_base_path(base_path, path)
        for method, op in item.items():
            if method.upper() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            operations.append(Operation(
                method=method.upper(),
                path=full,
                shape=normalize_path(full),
                operation_id=op.get("operationId"),
                summary=op.get("summary"),
                deprecated=bool(op.get("deprecated")),
            ))
    return operations


def _segments_match(observed: list[str], spec: list[str]) -> int | None:
    """None if no match; otherwise how many literal segments agree (higher
    is a more specific match)."""
    if len(observed) != len(spec):
        return None
    literal = 0
    for o, s in zip(observed, spec):
        if s == PARAM:
            continue
        if o != s:
            return None
        literal += 1
    return literal


def match_operation(method: str, route: str, operations: list[Operation]) -> Operation | None:
    observed = normalize_path(route).split("/")
    best, best_score = None, -1
    for op in operations:
        if op.method != method:
            continue
        score = _segments_match(observed, op.shape.split("/"))
        if score is not None and score > best_score:
            best, best_score = op, score
    return best


@dataclass
class Traffic:
    method: str
    route: str
    requests: int
    errors: int
    last_seen: object  # datetime

    def describe(self) -> dict:
        return {
            "method": self.method,
            "route": self.route,
            "requests": self.requests,
            "error_rate": round(self.errors / self.requests, 4) if self.requests else None,
            "last_seen": self.last_seen,
        }


@dataclass
class DriftReport:
    operations: int = 0
    documented_in_use: int = 0
    undocumented: list[dict] = field(default_factory=list)
    dead: list[dict] = field(default_factory=list)
    deprecated_in_use: list[dict] = field(default_factory=list)
    unmatched_requests: int = 0
    undocumented_requests: int = 0
    total_requests: int = 0

    def to_dict(self) -> dict:
        return {
            "operations": self.operations,
            "documented_in_use": self.documented_in_use,
            "coverage": round(self.documented_in_use / self.operations, 4) if self.operations else None,
            "total_requests": self.total_requests,
            "undocumented_requests": self.undocumented_requests,
            "unmatched_requests": self.unmatched_requests,
            "undocumented": self.undocumented,
            "dead": self.dead,
            "deprecated_in_use": self.deprecated_in_use,
        }


def compare(operations: list[Operation], traffic: list[Traffic]) -> DriftReport:
    report = DriftReport(operations=len(operations))
    used: dict[int, list[Traffic]] = {}
    for t in traffic:
        report.total_requests += t.requests
        if t.route == UNMATCHED_ROUTE:
            report.unmatched_requests += t.requests
            continue
        op = match_operation(t.method, t.route, operations)
        if op is not None:
            used.setdefault(id(op), []).append(t)
        elif t.method not in IMPLICIT_METHODS:
            report.undocumented_requests += t.requests
            report.undocumented.append(t.describe())

    for op in operations:
        hits = used.get(id(op))
        if not hits:
            report.dead.append(op.describe())
            continue
        report.documented_in_use += 1
        if op.deprecated:
            requests = sum(h.requests for h in hits)
            report.deprecated_in_use.append({
                **op.describe(),
                "requests": requests,
                "last_seen": max(h.last_seen for h in hits),
                "routes": sorted({h.route for h in hits}),
            })

    report.undocumented.sort(key=lambda d: -d["requests"])
    report.deprecated_in_use.sort(key=lambda d: -d["requests"])
    report.dead.sort(key=lambda d: (d["path"], d["method"]))
    return report
