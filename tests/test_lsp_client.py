import asyncio
import pytest


@pytest.mark.asyncio
async def test_smoke_async():
    # simple async smoke test to ensure pytest-asyncio integration works
    await asyncio.sleep(0.001)
    assert True
