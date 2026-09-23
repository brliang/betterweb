from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api import admin, auth, catalog, feed, feedback, health, pins, preferences, survey
from app.db.session import create_engine, create_sessionmaker
from app.providers.embeddings import OpenRouterEmbeddings
from app.providers.openrouter import OpenRouterClient
from app.providers.spend import SpendMeter
from app.search import QueryEmbedder
from app.settings import get_settings


def _operation_id(route: APIRoute) -> str:
    # Operation IDs become the generated client's function and hook names (e.g. `useHealth`).
    return route.name


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The database engine, and the search embedder when a provider key is set."""
    settings = get_settings()
    engine = create_engine(settings)
    app.state.sessionmaker = create_sessionmaker(engine)
    async with AsyncExitStack() as stack:
        stack.push_async_callback(engine.dispose)
        key = settings.openrouter_api_key
        if key is not None and key.get_secret_value():
            client = await stack.enter_async_context(OpenRouterClient(settings))

            def provider(meter: SpendMeter) -> OpenRouterEmbeddings:
                return OpenRouterEmbeddings(client, settings, meter)

            app.state.query_embedder = QueryEmbedder(settings, provider)
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Discovery Engine",
        version="0.1.0",
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    for module in (health, auth, catalog, survey, preferences, pins, feed, feedback, admin):
        app.include_router(module.router)
    return app


app = create_app()
