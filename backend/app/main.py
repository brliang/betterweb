from typing import Literal

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel


def _operation_id(route: APIRoute) -> str:
    # Operation IDs become the generated client's function and hook names (e.g. `useHealth`).
    return route.name


app = FastAPI(title="Discovery Engine", version="0.1.0", generate_unique_id_function=_operation_id)


class Health(BaseModel):
    status: Literal["ok"]


@app.get("/health")
def health() -> Health:
    return Health(status="ok")
