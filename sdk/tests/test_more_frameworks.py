"""Starlette, Litestar and Django integrations. Each test makes real requests
through the framework and checks the events that reach a fake collector."""

import asyncio
import time

import pytest

import reqly


def _events(client, batches):
    client._buffer.flush()
    time.sleep(0.05)
    return [e for b in batches for e in b["events"]]


def _summary(events):
    return [(e["method"], e["route"], e["status_code"], e["error"]) for e in events]


# --------------------------------------------------------------- Starlette

def test_starlette_records_route_templates_including_mounts(fake_collector):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    from starlette.testclient import TestClient

    async def user(request):
        return JSONResponse({"id": request.path_params["user_id"]})

    async def item(request):
        return JSONResponse({})

    app = Starlette(routes=[
        Route("/users/{user_id}", user),
        Mount("/api/v1", routes=[Route("/items/{item_id:int}", item, methods=["POST"])]),
    ])
    url, batches = fake_collector
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    try:
        tc = TestClient(app)
        tc.get("/users/42")
        tc.post("/api/v1/items/7")
        tc.get("/nope")
        events = _events(client, batches)
    finally:
        client.shutdown()

    assert _summary(events) == [
        ("GET", "/users/{user_id}", 200, False),
        ("POST", "/api/v1/items/{item_id:int}", 200, False),
        ("GET", "__unmatched__", 404, False),
    ]


def test_fastapi_mounted_sub_app_gets_the_mount_prefix(fake_collector):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    sub = FastAPI()

    @sub.get("/items/{item_id}")
    def item(item_id: int):
        return {"id": item_id}

    router = APIRouter(prefix="/v2")

    @router.get("/users/{user_id}")
    def user(user_id: int):
        return {"id": user_id}

    app = FastAPI()
    app.include_router(router)
    app.mount("/api", sub)
    url, batches = fake_collector
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    try:
        tc = TestClient(app)
        tc.get("/api/items/3")
        tc.get("/v2/users/9")
        events = _events(client, batches)
    finally:
        client.shutdown()

    assert [e["route"] for e in events] == ["/api/items/{item_id}", "/v2/users/{user_id}"]


def test_subclassed_fastapi_app_is_detected_as_fastapi():
    from fastapi import FastAPI

    class MyApp(FastAPI):
        pass

    assert reqly._detect_framework(MyApp()) == "fastapi"


# ---------------------------------------------------------------- Litestar

def test_litestar_records_path_templates_and_errors(fake_collector):
    from litestar import Litestar, get
    from litestar.testing import TestClient

    @get("/items/{item_id:int}")
    async def item(item_id: int) -> dict:
        return {"id": item_id}

    @get("/boom")
    async def boom() -> dict:
        raise RuntimeError("kaboom")

    app = Litestar([item, boom])
    url, batches = fake_collector
    client = reqly.instrument(app, service_name="s", collector_url=url, flush_interval_seconds=999)
    assert reqly._detect_framework(app) == "litestar"
    try:
        with TestClient(app=app) as tc:
            tc.get("/items/5")
            tc.get("/boom")
            tc.get("/missing")
        events = _events(client, batches)
    finally:
        client.shutdown()

    assert _summary(events) == [
        ("GET", "/items/{item_id}", 200, False),
        ("GET", "/boom", 500, True),
        ("GET", "__unmatched__", 404, False),
    ]


# ------------------------------------------------------------------ Django

def _django_views():
    from django.http import HttpResponse, JsonResponse

    def user(request, pk):
        return JsonResponse({"id": pk})

    def drf_style(request, pk):
        return JsonResponse({"id": pk})

    def boom(request):
        raise ValueError("kaboom")

    async def async_item(request, item_id):
        return HttpResponse(b"x" * 10)

    def created(request):
        import reqly

        reqly.record_llm_usage("gpt-4o", 7, 3)
        return HttpResponse(status=201)

    return user, drf_style, boom, async_item, created


urlpatterns = []  # filled in by the django_app fixture (this module is ROOT_URLCONF)


@pytest.fixture
def django_app(fake_collector):
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=False,
            ALLOWED_HOSTS=["testserver"],
            ROOT_URLCONF=__name__,
            MIDDLEWARE=["reqly.integrations.django.ReqlyMiddleware"],
            SECRET_KEY="test",
            INSTALLED_APPS=[],
        )
        django.setup()

    from django.urls import include, path, re_path

    user, drf_style, boom, async_item, created = _django_views()
    urlpatterns[:] = [
        path("users/<int:pk>/", user),
        re_path(r"^api/legacy/(?P<pk>[^/.]+)/$", drf_style),
        path("boom/", boom),
        path("v2/", include([path("items/<slug:item_id>", async_item), path("orders", created)])),
    ]

    from reqly.integrations import django as reqly_django

    url, batches = fake_collector
    settings.REQLY = {"service_name": "django-svc", "collector_url": url, "flush_interval_seconds": 999}
    reqly_django._client = None
    yield batches
    if reqly_django._client is not None:
        reqly_django._client.shutdown()
        reqly_django._client = None


def test_django_sync_routes_errors_and_includes(django_app):
    from django.test import Client

    from reqly.integrations import django as reqly_django

    batches = django_app
    c = Client(raise_request_exception=False)
    c.get("/users/42/")
    c.get("/api/legacy/abc/")
    c.get("/boom/")
    c.post("/v2/orders", data=b"hello", content_type="text/plain")
    c.get("/nope/")
    events = _events(reqly_django._client, batches)

    assert _summary(events) == [
        ("GET", "/users/{pk}/", 200, False),
        ("GET", "/api/legacy/{pk}/", 200, False),
        ("GET", "/boom/", 500, True),
        ("POST", "/v2/orders", 201, False),
        ("GET", "__unmatched__", 404, False),
    ]
    assert events[2]["error_type"] == "ValueError"
    assert events[3]["request_bytes"] == 5
    assert batches[0]["service_name"] == "django-svc"


def test_django_async_view(django_app):
    from django.test import AsyncClient

    from reqly.integrations import django as reqly_django

    batches = django_app

    async def run():
        response = await AsyncClient().get("/v2/items/blue-shirt")
        return response.status_code

    assert asyncio.run(run()) == 200
    events = _events(reqly_django._client, batches)
    assert _summary(events) == [("GET", "/v2/items/{item_id}", 200, False)]
    assert events[0]["response_bytes"] == 10


def test_django_consumer_and_llm_usage(django_app):
    import hashlib
    import hmac

    from django.conf import settings
    from django.test import Client

    from reqly.integrations import django as reqly_django

    batches = django_app
    settings.REQLY = {**settings.REQLY, "consumer_header": "X-API-Key", "consumer_salt": "s3cret"}
    Client().post("/v2/orders", HTTP_X_API_KEY="key_1")
    [event] = _events(reqly_django._client, batches)
    assert event["consumer_id"] == hmac.new(b"s3cret", b"key_1", hashlib.sha256).hexdigest()[:16]
    assert (event["llm_model"], event["llm_input_tokens"], event["llm_output_tokens"]) == ("gpt-4o", 7, 3)


@pytest.mark.parametrize("route, expected", [
    ("users/<int:pk>/", "/users/{pk}/"),
    ("users/<pk>/orders/<uuid:order_id>", "/users/{pk}/orders/{order_id}"),
    ("^api/users/(?P<pk>[^/.]+)/$", "/api/users/{pk}/"),
    ("^api/users/(?P<pk>[^/.]+)\\.(?P<format>[a-z0-9]+)/?$", "/api/users/{pk}.{format}/?"),
    ("", "/"),
])
def test_normalize_django_route(route, expected):
    pytest.importorskip("django")
    from reqly.integrations.django import normalize_django_route

    assert normalize_django_route(route) == expected
