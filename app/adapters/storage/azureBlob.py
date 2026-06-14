import logging
from typing import Dict, List, Optional

from app.ports.blobStorage import BlobStoragePort

logger = logging.getLogger(__name__)


class AzureBlobAdapter(BlobStoragePort):
    """
    Azure Blob Storage adapter using the official async SDK.

    Requires: pip install azure-storage-blob
    AZURE_STORAGE_CONNECTION_STRING format:
        DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=...
    """

    def __init__(
        self,
        connection_string: str,
        container_name: str,
        prefix: str = "",
    ) -> None:
        try:
            from azure.storage.blob.aio import BlobServiceClient

            self._service = BlobServiceClient.from_connection_string(connection_string)
        except ImportError:
            raise RuntimeError(
                "azure-storage-blob not installed — run: pip install azure-storage-blob"
            )
        self._container = container_name
        self._prefix = prefix.rstrip("/")

    def _blob_name(self, path: str) -> str:
        return f"{self._prefix}/{path.lstrip('/')}" if self._prefix else path.lstrip("/")

    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        blob_name = self._blob_name(path)
        container = self._service.get_container_client(self._container)
        blob = container.get_blob_client(blob_name)
        await blob.upload_blob(
            data,
            overwrite=True,
            content_settings={"content_type": content_type},
            metadata=metadata,
        )
        return f"azure://{self._container}/{blob_name}"

    async def get(self, path: str) -> bytes:
        blob_name = self._blob_name(path)
        container = self._service.get_container_client(self._container)
        blob = container.get_blob_client(blob_name)
        stream = await blob.download_blob()
        return await stream.readall()

    async def list(self, prefix: str) -> List[str]:
        full_prefix = self._blob_name(prefix)
        container = self._service.get_container_client(self._container)
        strip = (self._prefix + "/") if self._prefix else ""
        result: List[str] = []
        async for b in container.list_blobs(name_starts_with=full_prefix):
            result.append(b.name.removeprefix(strip))
        return result

    async def delete(self, path: str) -> None:
        blob_name = self._blob_name(path)
        container = self._service.get_container_client(self._container)
        blob = container.get_blob_client(blob_name)
        await blob.delete_blob()

    async def close(self) -> None:
        await self._service.close()
