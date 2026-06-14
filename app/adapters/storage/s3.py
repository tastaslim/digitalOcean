import asyncio
import logging
from typing import Dict, List, Optional

from app.ports.blobStorage import BlobStoragePort

logger = logging.getLogger(__name__)


class S3Adapter(BlobStoragePort):
    """
    AWS S3 blob storage adapter.

    All boto3 calls are dispatched to a thread-pool executor so they do not
    block the asyncio event loop.

    Requires: pip install boto3
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 not installed — run: pip install boto3")

        session = boto3.Session(
            region_name=region,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        kwargs = {"endpoint_url": endpoint_url} if endpoint_url else {}
        self._client = session.client("s3", **kwargs)
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _key(self, path: str) -> str:
        return f"{self._prefix}/{path.lstrip('/')}" if self._prefix else path.lstrip("/")

    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        key = self._key(path)
        kwargs: dict = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if metadata:
            kwargs["Metadata"] = metadata
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self._client.put_object(**kwargs))
        return f"s3://{self._bucket}/{key}"

    async def get(self, path: str) -> bytes:
        key = self._key(path)
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: self._client.get_object(Bucket=self._bucket, Key=key)
        )
        return response["Body"].read()

    async def list(self, prefix: str) -> List[str]:
        full_prefix = self._key(prefix)
        loop = asyncio.get_running_loop()
        paginator = self._client.get_paginator("list_objects_v2")
        pages = await loop.run_in_executor(
            None,
            lambda: list(paginator.paginate(Bucket=self._bucket, Prefix=full_prefix)),
        )
        result: List[str] = []
        strip = (self._prefix + "/") if self._prefix else ""
        for page in pages:
            for obj in page.get("Contents", []):
                result.append(obj["Key"].removeprefix(strip))
        return result

    async def delete(self, path: str) -> None:
        key = self._key(path)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key),
        )

    async def close(self) -> None:
        pass
