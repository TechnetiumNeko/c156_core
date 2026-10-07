"""Application construction and read-only startup validation."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import secrets
import shlex

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from ..core.errors import ContentError
from ..core.models import ContentScope
from ..services.content import ContentService
from ..services.identity import IdentityService
from ..services.unit_of_work import ApplicationUnitOfWork
from ..storage import Database
from ..storage.errors import StorageError
from .config import ServerConfig


@dataclass(frozen=True)
class ServerServices:
    content: ContentService
    identity: IdentityService
    scope: ContentScope
    nonce: str


def _services(config):
    database = Database(config.database_path)
    try:
        with ApplicationUnitOfWork(database).transaction() as work:
            scope = work.default_scope()
    except (ContentError, StorageError, OSError, ValueError) as error:
        path = shlex.quote(str(config.database_path))
        raise RuntimeError(
            f'无法启动 C156 服务：{error}\n'
            f'显式初始化新库：python -m src.storage init --database {path}\n'
            f'导入旧数据：python -m src.storage migrate-legacy --source data --database {path}\n'
            f'已有空库的首个管理员：python -m src.identity bootstrap-admin --database {path} '
            '--login-name admin --display-name 管理员'
        ) from error
    return ServerServices(ContentService(database), IdentityService(database), scope, secrets.token_urlsafe(32))


def create_app(config: ServerConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.services = await run_in_threadpool(_services, config)
        try:
            yield
        finally:
            del app.state.services

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    return app
