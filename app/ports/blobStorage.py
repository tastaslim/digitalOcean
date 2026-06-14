from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BlobStoragePort(ABC):
    """
    Port for storing and retrieving binary blobs (JSON archives, raw LLM payloads).

    Implementations: LocalFileAdapter (dev/test), S3Adapter, AzureBlobAdapter.

    Used to archive every shadow event for offline replay and regression testing.
    """

    @abstractmethod
    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """Store data at path. Returns the resolved URI (file://, s3://, azure://)."""

    @abstractmethod
    async def get(self, path: str) -> bytes:
        """Retrieve data stored at path."""

    @abstractmethod
    async def list(self, prefix: str) -> List[str]:
        """Return all paths whose key starts with prefix."""

    @abstractmethod
    async def delete(self, path: str) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
