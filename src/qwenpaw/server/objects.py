"""S3 object adapter. No user-controlled buckets, URLs or filesystem paths."""

import asyncio


class S3Objects:
    def __init__(self, config):
        import boto3

        self.bucket = config.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=config.s3_endpoint,
            region_name=config.s3_region,
        )

    async def put(self, key: str, content: bytes):
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=content
        )

    async def get(self, key: str) -> bytes:
        def read():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            with response["Body"] as stream:
                return stream.read()

        return await asyncio.to_thread(read)

    async def delete(self, key: str):
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def url(self, key: str) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=300,
        )


class MemoryObjects:
    """Bounded local-development storage, private to the in-process API.

    Never used by production serve. Access is only through owned file IDs.
    """

    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.items = {}
        self.max_bytes = max_bytes
        self.size = 0

    async def put(self, key, content):
        from .contracts import Conflict

        size = self.size - len(self.items.get(key, b"")) + len(content)
        if size > self.max_bytes:
            raise Conflict("Local file storage is full; delete unused files")
        self.items[key] = bytes(content)
        self.size = size

    async def get(self, key):
        from .contracts import NotFound

        if key not in self.items:
            raise NotFound("File not found")
        return self.items[key]

    async def delete(self, key):
        self.size -= len(self.items.pop(key, b""))

    async def url(self, key):
        return None  # Download via authenticated /v1/files/{id}/content.
