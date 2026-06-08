from app.resources.proxy.proxyRoute import proxyRoute
from app.resources.metrics.metricsRoute import metricsRoute
from app.resources.config.configRoute import configRoute

routers = [
    proxyRoute,
    metricsRoute,
    configRoute,
]
