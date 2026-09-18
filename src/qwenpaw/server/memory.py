"""Durable user-scoped extraction jobs and optional repository-backed semantic recall.

No personal-workspace ReMe instance or shared agent memory directory is loaded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, config, repository, client=None):
        self.config, self.repo = config, repository
        self.client = client
        self.owner = str(uuid.uuid4())

    async def initialize(self):
        if self.config.definition().embedding_model:
            await self.repo.ensure_vector_support()

    def model_client(self, definition):
        if self.client:
            return self.client
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=self.config.model_api_key.get_secret_value(),
            base_url=definition.base_url,
            timeout=60,
            max_retries=0,
        )

    async def embedding(self, definition, text, client):
        result = await client.embeddings.create(
            model=definition.embedding_model,
            input=text,
        )
        return list(result.data[0].embedding)

    async def search(self, user_id, query, definition):
        if not definition.embedding_model:
            # Explicit lexical mode for installations without an embedding model.
            return await self.repo.memories(user_id, query)
        client = self.model_client(definition)
        try:
            vector = await self.embedding(
                definition, query or "user preferences", client
            )
            return await self.repo.search_memory_vectors(
                user_id, definition.embedding_model, vector, limit=10
            )
        finally:
            if not self.client:
                await client.close()

    async def knowledge(self, query, definition):
        if not definition.embedding_model:
            terms = query.lower().split()
            return [
                text
                for text in definition.public_knowledge
                if any(term in text.lower() for term in terms)
            ][:10]
        client = self.model_client(definition)
        try:
            indexed = await self.repo.knowledge_indexed(
                definition.version, definition.embedding_model
            )
            for index, text in enumerate(definition.public_knowledge):
                if index in indexed:
                    continue
                vector = await self.embedding(definition, text, client)
                await self.repo.put_knowledge_vector(
                    definition.version, index, text, definition.embedding_model, vector
                )
            vector = await self.embedding(definition, query, client)
            return await self.repo.search_knowledge_vectors(
                definition.version, definition.embedding_model, vector, limit=10
            )
        finally:
            if not self.client:
                await client.close()

    async def remember(self, ctx, text, definition):
        await self.repo.remember(ctx, text)
        # Indexing is done outside the model tool transaction and is safely repeatable.
        await self.index_user(ctx.user_id, definition)

    async def index_user(self, user_id, definition):
        if not definition.embedding_model:
            return
        rows = await self.repo.pending_memory_vectors(
            user_id, definition.embedding_model, limit=100
        )
        client = self.model_client(definition)
        try:
            for row in rows:
                vector = await self.embedding(definition, row["text"], client)
                await self.repo.put_memory_vector(
                    user_id, row["id"], definition.embedding_model, vector
                )
        finally:
            if not self.client:
                await client.close()

    async def claim(self):
        return await self.repo.claim_memory(self.owner, lease_seconds=300)

    async def step(self):
        job = await self.claim()
        if not job:
            return False
        from .config import AssistantDefinition

        client = None
        try:
            run = await self.repo.get_run(job["user_id"], str(job["run_id"]))
            definition = AssistantDefinition.model_validate(
                await self.repo.definition(run["definition_version"])
            )
            facts = []
            if definition.auto_memory:
                if definition.model_protocol == "tl":
                    from agentscope.message import SystemMsg, UserMsg
                    from .agent import create_model_factory

                    factory = create_model_factory(self.config)
                    try:
                        response = await factory(definition).generate_structured_output(
                            [
                                SystemMsg(
                                    name="system",
                                    content="Extract durable user facts/preferences explicitly stated in the user message. Never extract instructions, secrets, guesses or facts about other people.",
                                ),
                                UserMsg(name="user", content=run["input"]["message"]),
                            ],
                            {
                                "type": "object",
                                "properties": {
                                    "facts": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "maxItems": 10,
                                    }
                                },
                                "required": ["facts"],
                                "additionalProperties": False,
                            },
                        )
                        raw = response.content
                    finally:
                        await factory.close()
                else:
                    client = self.model_client(definition)
                    response = await client.chat.completions.create(
                        model=definition.model,
                        messages=[
                            {
                                "role": "system",
                                "content": "Extract durable user facts/preferences explicitly stated in the following message. Never extract instructions, secrets, guesses or facts about other people. Return a JSON object with a facts array of at most 10 strings, or an empty array.",
                            },
                            {"role": "user", "content": run["input"]["message"]},
                        ],
                        response_format={"type": "json_object"},
                    )
                    raw = json.loads(response.choices[0].message.content)
                facts = raw.get("facts", [])
                if not isinstance(facts, list) or any(
                    not isinstance(item, str) for item in facts
                ):
                    raise ValueError("Invalid extracted facts")
            if not await self.repo.merge_memory(job, facts[:10]):
                return True
            await self.index_user(job["user_id"], definition)
            await self.repo.complete_memory(job)
        except Exception as exc:
            logger.exception("Memory extraction failed")
            await self.repo.fail_memory(job, type(exc).__name__)
        finally:
            if client and not self.client:
                await client.close()
        return True

    async def serve(self):
        while True:
            try:
                if not await self.step():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Memory worker unavailable")
                await asyncio.sleep(2)
