from fastapi import Request

from app.common.circuitBreaker import CircuitBreaker
from app.infrastructure.container import Container
from app.ports.blobStorage import BlobStoragePort
from app.ports.cache import CachePort
from app.ports.database import MismatchRepository, ModelFleetRepository
from app.ports.llm import LlmPort
from app.ports.messageQueue import MessageQueuePort
from app.resources.config.configService import ConfigService
from app.resources.metrics.metricsService import MetricsService


# ------------------------------------------------------------------
# Container accessor — everything flows through here
# ------------------------------------------------------------------

def getContainer(request: Request) -> Container:
    return request.app.state.container


# ------------------------------------------------------------------
# Port accessors
# ------------------------------------------------------------------

def getQueue(request: Request) -> MessageQueuePort:
    return request.app.state.container.queue


def getCache(request: Request) -> CachePort:
    return request.app.state.container.cache


def getMismatchRepository(request: Request) -> MismatchRepository:
    return request.app.state.container.mismatchRepository


def getModelFleetRepository(request: Request) -> ModelFleetRepository:
    return request.app.state.container.modelFleetRepository


def getStorage(request: Request) -> BlobStoragePort:
    return request.app.state.container.storage


# ------------------------------------------------------------------
# Service accessors — constructed once per request, stateless wrappers
# ------------------------------------------------------------------

def getMetricsService(request: Request) -> MetricsService:
    return MetricsService(cache=request.app.state.container.cache)


def getConfigService(request: Request) -> ConfigService:
    return ConfigService(cache=request.app.state.container.cache)


def getCircuitBreaker(request: Request) -> CircuitBreaker:
    return request.app.state.container.circuitBreaker


def getPrimaryLlm(request: Request) -> LlmPort:
    return request.app.state.container.primaryLlm
