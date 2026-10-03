import pytest

from threadsong.config import Settings
from threadsong.db import JobStore
from threadsong.domain import SongRequest


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        local_data_dir=tmp_path,
        run_worker=False,
    )


@pytest.fixture
async def store(settings):
    db = JobStore(settings.database_url.get_secret_value())
    await db.initialize_demo()
    yield db
    await db.close()


@pytest.fixture
def song_request():
    return SongRequest(
        platform="ando",
        event_id="evt-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        message_id="request-1",
        thread_id="root-1",
        author_id="human-1",
    )
