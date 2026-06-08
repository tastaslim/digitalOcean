import pytest

from app.resources.config.configService import RuntimeConfig


@pytest.fixture
def cfg() -> RuntimeConfig:
    return RuntimeConfig()


def test_default_shadowPercentage(cfg: RuntimeConfig) -> None:
    assert cfg.shadowPercentage == 100.0


async def test_update_changes_percentage(cfg: RuntimeConfig) -> None:
    await cfg.update(shadowPercentage=50.0)
    assert cfg.shadowPercentage == 50.0


async def test_update_to_zero(cfg: RuntimeConfig) -> None:
    await cfg.update(shadowPercentage=0.0)
    assert cfg.shadowPercentage == 0.0


async def test_snapshot_reflects_current_value(cfg: RuntimeConfig) -> None:
    await cfg.update(shadowPercentage=75.5)
    snap = cfg.snapshot()
    assert snap["shadowPercentage"] == 75.5


def test_snapshot_default(cfg: RuntimeConfig) -> None:
    snap = cfg.snapshot()
    assert snap["shadowPercentage"] == 100.0
