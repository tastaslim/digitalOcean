"""
OpenTelemetry wiring — distributed tracing for the proxy and the worker.

Design goals:
  * Zero cost when off. Nothing here imports OpenTelemetry at module load. When
    TELEMETRY_ENABLED is false (the default, including all tests) every helper is
    a no-op and no OTel package is touched.
  * One trace across the SNS->SQS hop. The proxy injects the active trace context
    into the queue message attributes (injectContext); the worker extracts it
    (linkedSpan) so the candidate call, S3 writes, and DB writes show up under
    the same trace as the originating /v1/chat request.
  * Auto-instrumentation does the heavy lifting. FastAPI, httpx (LLM calls), boto
    (SNS/SQS/S3), asyncpg, and redis are instrumented automatically, so most
    spans appear without any manual span code.

Enable with:  TELEMETRY_ENABLED=true  OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
"""

import logging
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

logger = logging.getLogger(__name__)

# Flipped true only after a provider is successfully installed. Every public
# helper short-circuits on this, so a disabled or failed setup costs nothing.
_enabled = False


def setupTelemetry(serviceName: str, *, endpoint: str) -> None:
    """Install a TracerProvider with an OTLP/HTTP exporter and auto-instrument
    the client libraries. Safe to call once per process; later calls are no-ops.

    Any failure (missing packages, bad endpoint) is logged and swallowed — a
    telemetry problem must never take the service down.
    """
    global _enabled
    if _enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "TELEMETRY_ENABLED but OpenTelemetry packages are not installed; "
            "tracing disabled. Install the opentelemetry-* requirements."
        )
        return

    try:
        resource = Resource.create({"service.name": serviceName})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _enabled = True
        _instrumentLibraries()
        logger.info(
            "Telemetry enabled — service=%s otlp=%s", serviceName, endpoint
        )
    except Exception:
        logger.exception("Telemetry setup failed; continuing without tracing")


def _instrumentLibraries() -> None:
    """Best-effort auto-instrumentation. Each block is independent so a missing
    optional instrumentor never blocks the others."""
    instrumentors = [
        ("httpx", "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("botocore", "opentelemetry.instrumentation.botocore", "BotocoreInstrumentor"),
        ("asyncpg", "opentelemetry.instrumentation.asyncpg", "AsyncPGInstrumentor"),
        ("redis", "opentelemetry.instrumentation.redis", "RedisInstrumentor"),
    ]
    for label, module, clsName in instrumentors:
        try:
            mod = __import__(module, fromlist=[clsName])
            getattr(mod, clsName)().instrument()
        except Exception:
            logger.debug("Could not instrument %s", label, exc_info=True)


def instrumentFastApi(app) -> None:
    """Attach FastAPI server-span instrumentation to the given app."""
    if not _enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.debug("Could not instrument FastAPI", exc_info=True)


def injectContext(carrier: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Inject the active trace context into a string carrier (e.g. queue message
    attributes) so a downstream consumer can continue the same trace. Returns the
    carrier unchanged when telemetry is off."""
    carrier = carrier or {}
    if not _enabled:
        return carrier
    try:
        from opentelemetry.propagate import inject

        inject(carrier)
    except Exception:
        logger.debug("Trace context injection failed", exc_info=True)
    return carrier


@contextmanager
def linkedSpan(
    name: str,
    carrier: Optional[Dict[str, str]] = None,
    attributes: Optional[Dict[str, object]] = None,
) -> Iterator[None]:
    """Run a block inside a span that is a child of the trace context found in
    *carrier* (the message attributes from the publisher). No-op when telemetry
    is off, so callers can wrap unconditionally."""
    if not _enabled:
        yield
        return
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import extract

        parent = extract(carrier) if carrier else None
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            name, context=parent, attributes=attributes or {}
        ):
            yield
    except Exception:
        logger.debug("linkedSpan %s failed; running without a span", name, exc_info=True)
        yield
