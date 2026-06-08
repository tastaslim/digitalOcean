from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.common.exceptionHandlers import registerExceptionHandlers
from app.core.database import initDb
from app.routes.allRoutes import routers


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    await initDb()
    yield


def createApp() -> FastAPI:
    app = FastAPI(title="LLM Shadow Proxy", lifespan=lifespan)
    registerExceptionHandlers(app)
    for router in routers:
        app.include_router(router=router)
    return app


app = createApp()
