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
