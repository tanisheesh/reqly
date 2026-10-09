from __future__ import annotations

from ..core.client import ReqlyClient
from .fastapi import ReqlyASGIMiddleware


def instrument_litestar(app, client: ReqlyClient) -> None:
    """Litestar builds its middleware stack when the app is constructed, so
    middleware can't be added afterwards the usual way. Instead the app's
    composed ASGI handler is wrapped in place: every request still passes
    through the whole Litestar stack, and Litestar records the matched route
    template in scope["path_template"]."""
    app.asgi_handler = ReqlyASGIMiddleware(app.asgi_handler, client)
