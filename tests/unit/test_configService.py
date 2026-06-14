import pytest

from app.adapters.cache.memory import InMemoryCacheAdapter
from app.resources.config.configService import ConfigService


@pytest.fixture
def cfg() -> ConfigService:
    return ConfigService(cache=InMemoryCacheAdapter())


async def test_default_shadowPercentage(cfg: ConfigService) -> None:
    assert await cfg.getShadowPercentage() == 100.0


async def test_update_changes_percentage(cfg: ConfigService) -> None:
    await cfg.update(shadowPercentage=50.0)
    assert await cfg.getShadowPercentage() == 50.0


async def test_update_to_zero(cfg: ConfigService) -> None:
    await cfg.update(shadowPercentage=0.0)
    assert await cfg.getShadowPercentage() == 0.0


async def test_snapshot_reflects_current_value(cfg: ConfigService) -> None:
    await cfg.update(shadowPercentage=75.5)
    snap = await cfg.snapshot()
    assert snap["shadowPercentage"] == 75.5


async def test_snapshot_default(cfg: ConfigService) -> None:
    snap = await cfg.snapshot()
    assert snap["shadowPercentage"] == 100.0
