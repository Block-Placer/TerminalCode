import asyncio
import pytest

@pytest.mark.asyncio
async def test_smoke_async():
    await asyncio.sleep(0.001)
    assert True