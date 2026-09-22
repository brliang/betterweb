from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api import health


def _operation_id(route: APIRoute) -> str:
    # Operation IDs become the generated client's function and hook names (e.g. `useHealth`).
    return route.name


def create_app() -> FastAPI:
    app = FastAPI(
        title="Discovery Engine", version="0.1.0", generate_unique_id_function=_operation_id
    )
    app.include_router(health.router)
    return app


app = create_app()
