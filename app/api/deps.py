from collections.abc import AsyncGenerator
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
