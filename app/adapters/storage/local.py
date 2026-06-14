import asyncio
import os
from typing import Dict, List, Optional

from app.ports.blobStorage import BlobStoragePort


class LocalFileAdapter(BlobStoragePort):
    """
    Local filesystem blob storage for dev and tests.

    Files are written under base_dir, preserving the path hierarchy of the
    storage key. Useful for inspecting archived shadow events without running
    a cloud provider.

    Not for production — no replication, no access control, no durability
    guarantees beyond the host filesystem.
    """

    def __init__(self, base_dir: str) -> None:
        self._base = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _full_path(self, path: str) -> str:
        return os.path.join(self._base, path.lstrip("/"))

    async def put(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        full = self._full_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: open(full, "wb").write(data))
        return f"file://{full}"

    async def get(self, path: str) -> bytes:
        full = self._full_path(path)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: open(full, "rb").read())

    async def list(self, prefix: str) -> List[str]:
        base_prefix = self._full_path(prefix)
        result: List[str] = []
        if not os.path.isdir(base_prefix):
            return result
        for root, _, files in os.walk(base_prefix):
            for f in files:
                full = os.path.join(root, f)
                result.append(full.removeprefix(self._base).lstrip("/"))
        return result

    async def delete(self, path: str) -> None:
        try:
            os.remove(self._full_path(path))
        except FileNotFoundError:
            pass

    async def close(self) -> None:
        pass
