import os

import pytest

# Обязательные переменные окружения для Config (тесты используют _env_file=None).
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("OPENROUTER_API_KEY", "sk-test")

from app.config import Config  # noqa: E402
from app.runtime_settings import Settings  # noqa: E402
from app.storage import Storage  # noqa: E402


@pytest.fixture
def config():
    return Config(_env_file=None)


@pytest.fixture
async def storage(tmp_path):
    st = Storage(str(tmp_path / "test.db"))
    await st.connect()
    yield st
    await st.close()


@pytest.fixture
async def settings(storage, config):
    s = Settings(storage, config)
    await s.load()
    return s
