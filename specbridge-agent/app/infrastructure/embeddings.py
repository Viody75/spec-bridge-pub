from __future__ import annotations

import asyncio
import functools

from google import genai
from google.genai import types

from app.config import get_settings


class EmbeddingService:
    def __init__(self) -> None:
        self._cache: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
        self._client: genai.Client | None = None

    @property
    def configured(self) -> bool:
        settings = get_settings()
        return bool(settings.text_embedding_model and settings.model_name)

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(
                vertexai=True,
                location="global",
            )
        return self._client

    async def embed(
        self, text: str, *, task_type: str = "RETRIEVAL_DOCUMENT"
    ) -> list[float] | None:
        model = get_settings().text_embedding_model
        if not model:
            return None
        cache_key = f"{model}:{task_type}:{text}"
        if cached := self._cache.get(cache_key):
            return cached
        async with self._lock:
            if cached := self._cache.get(cache_key):
                return cached
            response = await self._get_client().aio.models.embed_content(
                model=model,
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            if not response.embeddings or not response.embeddings[0].values:
                return None
            values = list(response.embeddings[0].values)
            if len(self._cache) >= 512:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = values
            return values


@functools.cache
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
