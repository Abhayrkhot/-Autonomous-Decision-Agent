import pytest


@pytest.fixture
def anyio_backend():
    # The application and concurrency tests use asyncio; Trio is not a dependency.
    return "asyncio"
