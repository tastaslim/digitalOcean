from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.common.exceptionHandlers import registerExceptionHandlers
from app.core.database import initDb
from app.routes.allRoutes import routers


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Runs startup logic (database initialisation) before yielding control to
    the application, then performs any teardown after the server shuts down.

    :param _app: The FastAPI application instance (unused directly).
    :type _app: FastAPI
    :return: Async generator that yields once after startup completes.
    :rtype: AsyncGenerator[None, None]
    """
    await initDb()
    yield


def createApp() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Registers global exception handlers and mounts all routers defined in
    :mod:`app.routes.allRoutes`.

    :return: Fully configured :class:`FastAPI` application instance.
    :rtype: FastAPI
    """
    app = FastAPI(title="LLM Shadow Proxy", lifespan=lifespan)
    registerExceptionHandlers(app)
    for router in routers:
        app.include_router(router=router)
    return app


app = createApp()
