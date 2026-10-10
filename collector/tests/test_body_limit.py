from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.body_limit import BodySizeLimitMiddleware
from app.main import app as collector_app


def _app(limit):
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=limit)

    @app.post("/echo")
    async def echo(request: Request):
        return {"bytes": len(await request.body())}

    return app


def test_small_bodies_pass():
    client = TestClient(_app(100))
    assert client.post("/echo", content=b"x" * 100).json() == {"bytes": 100}


def test_declared_length_over_the_limit_is_refused_up_front():
    client = TestClient(_app(100))
    response = client.post("/echo", content=b"x" * 101)
    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_chunked_body_is_cut_off_while_streaming():
    def chunks():
        for _ in range(50):
            yield b"x" * 10  # 500 bytes, no Content-Length

    client = TestClient(_app(100))
    response = client.post("/echo", content=chunks())
    assert response.status_code == 413


def test_collector_has_the_limit():
    assert any(m.cls is BodySizeLimitMiddleware for m in collector_app.user_middleware)
