"""ASGI-to-Lambda adapter for deploying the collector behind API Gateway.

Not used by the Docker Compose path (that runs uvicorn directly) -- this is
only the entry point referenced by infra/sam/template.yaml. See
ARCHITECTURE.md §8 for the cold-start and connection-pooling caveats that
apply when running the collector this way.
"""

from mangum import Mangum

from .main import app

handler = Mangum(app, lifespan="auto")
