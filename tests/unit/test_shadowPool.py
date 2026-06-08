import asyncio

import pytest

from app.core.shadowPool import ShadowPool


async def test_submit_within_capacity_runs_coro() -> None:
    pool = ShadowPool(maxConcurrent=2)
    ran: list[int] = []

    async def coro() -> None:
        ran.append(1)

    submitted = await pool.submit(coro())
    await asyncio.sleep(0.05)

    assert submitted is True
    assert ran == [1]


async def test_submit_at_capacity_sheds() -> None:
    pool = ShadowPool(maxConcurrent=1)
    blocking = asyncio.Event()
    ran: list[int] = []

    async def slow() -> None:
        await blocking.wait()
        ran.append(1)

    # Fill the single slot
    first = await pool.submit(slow())
    assert first is True
    assert pool.active == 1

    # Second submit must be shed
    second = await pool.submit(slow())
    assert second is False

    blocking.set()
    await asyncio.sleep(0.05)
    assert pool.active == 0


async def test_active_count_decrements_after_completion() -> None:
    pool = ShadowPool(maxConcurrent=5)

    async def coro() -> None:
        await asyncio.sleep(0.02)

    await pool.submit(coro())
    assert pool.active == 1
    await asyncio.sleep(0.1)
    assert pool.active == 0


async def test_pool_accepts_new_tasks_after_slot_freed() -> None:
    pool = ShadowPool(maxConcurrent=1)
    done: list[int] = []

    async def coro(n: int) -> None:
        done.append(n)

    await pool.submit(coro(1))
    await asyncio.sleep(0.05)  # first task finishes, slot freed

    submitted = await pool.submit(coro(2))
    await asyncio.sleep(0.05)

    assert submitted is True
    assert 2 in done


async def test_concurrent_submits_respect_cap() -> None:
    """Concurrent submits must not exceed maxConcurrent even under a race."""
    pool = ShadowPool(maxConcurrent=3)
    blocking = asyncio.Event()

    async def slow() -> None:
        await blocking.wait()

    results = await asyncio.gather(*(pool.submit(slow()) for _ in range(6)))

    accepted = sum(results)
    assert accepted == 3
    assert pool.active == 3

    blocking.set()
    await asyncio.sleep(0.05)
    assert pool.active == 0
