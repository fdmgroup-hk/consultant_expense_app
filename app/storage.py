"""Where the original uploaded files are kept.

The extracted chunks in the database are what the app actually searches, so
retention is about being able to re-extract later - if the chunker improves, or
someone wants the original deck back.

Three backends, chosen by ``STORAGE_BACKEND``:

* ``local`` - a folder on disk. The default, and correct for local development.
              **Wrong for Render/Fly free tiers**, whose filesystems are wiped on
              every deploy and restart.
* ``s3``    - any S3-compatible object store: Supabase Storage, Cloudflare R2,
              MinIO. This is what the hosted deployment uses.
* ``none``  - keep nothing. The knowledge base still works; you just cannot
              re-extract from source later.
"""
from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    pass


class Storage(ABC):
    name: str

    @abstractmethod
    def put(self, key: str, source: Path) -> str | None:
        """Store the file. Returns the key to record, or None if not retained."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def describe(self) -> str: ...

    @property
    def retains_originals(self) -> bool:
        return True


class NullStorage(Storage):
    name = "none"

    def put(self, key: str, source: Path) -> str | None:
        return None

    def get(self, key: str) -> bytes:
        raise StorageError(
            "Original files are not retained (STORAGE_BACKEND=none). "
            "The indexed text is still searchable."
        )

    def delete(self, key: str) -> None:
        return None

    def describe(self) -> str:
        return "originals not retained"

    @property
    def retains_originals(self) -> bool:
        return False


class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are app-generated ("documents/<uuid>.pptx"), but never let one
        # escape the root regardless.
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise StorageError("Refusing to touch a path outside the storage root.")
        return candidate

    def put(self, key: str, source: Path) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"No stored original for {key}.")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def describe(self) -> str:
        return f"local disk at {self.root}"


class S3Storage(Storage):
    """S3-compatible object storage - Supabase Storage, Cloudflare R2, MinIO."""

    name = "s3"

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        settings = get_settings()
        missing = [
            field for field, value in {
                "S3_BUCKET": settings.s3_bucket,
                "S3_ACCESS_KEY_ID": settings.s3_access_key_id,
                "S3_SECRET_ACCESS_KEY": settings.s3_secret_access_key,
            }.items() if not value
        ]
        if missing:
            raise StorageError(f"STORAGE_BACKEND=s3 but these are unset: {', '.join(missing)}")

        self.bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put(self, key: str, source: Path) -> str:
        try:
            self._client.upload_file(str(source), self.bucket, key)
        except Exception as exc:
            raise StorageError(f"Upload to {self.bucket}/{key} failed: {exc}") from exc
        return key

    def get(self, key: str) -> bytes:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            raise StorageError(f"No stored original for {key}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            logger.warning("Could not delete %s from %s", key, self.bucket, exc_info=True)

    def describe(self) -> str:
        return f"s3 bucket '{self.bucket}'"


class SupabaseStorage(Storage):
    """Supabase Storage over its REST API.

    Preferred over the ``s3`` backend when hosting on Supabase: it authenticates
    with the service-role key, which can be read straight from the project, so
    there are no separate S3 access keys to create by hand.
    """

    name = "supabase"

    def __init__(self) -> None:
        settings = get_settings()
        missing = [
            field for field, value in {
                "SUPABASE_URL": settings.supabase_url,
                "SUPABASE_SERVICE_KEY": settings.supabase_service_key,
            }.items() if not value
        ]
        if missing:
            raise StorageError(f"STORAGE_BACKEND=supabase but these are unset: {', '.join(missing)}")

        self.bucket = settings.supabase_bucket
        self._base = f"{settings.supabase_url.rstrip('/')}/storage/v1/object"
        self._key = settings.supabase_service_key

    def _request(self, method: str, key: str, payload: bytes | None = None) -> bytes:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self._base}/{self.bucket}/{key}", data=payload, method=method
        )
        request.add_header("Authorization", f"Bearer {self._key}")
        request.add_header("apikey", self._key)
        if payload is not None:
            request.add_header("Content-Type", "application/octet-stream")
            request.add_header("x-upsert", "true")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise StorageError(
                f"Supabase Storage {method} {self.bucket}/{key} failed: "
                f"HTTP {exc.code} {exc.read()[:200].decode('utf-8', 'replace')}"
            ) from exc
        except Exception as exc:
            raise StorageError(f"Supabase Storage unreachable: {exc}") from exc

    def put(self, key: str, source: Path) -> str:
        self._request("POST", key, source.read_bytes())
        return key

    def get(self, key: str) -> bytes:
        return self._request("GET", key)

    def delete(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except StorageError:
            logger.warning("Could not delete %s from %s", key, self.bucket, exc_info=True)

    def describe(self) -> str:
        return f"supabase bucket '{self.bucket}'"


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    settings = get_settings()
    backend = settings.storage_backend.lower().strip()

    if backend == "none":
        storage: Storage = NullStorage()
    elif backend == "supabase":
        try:
            storage = SupabaseStorage()
        except Exception as exc:
            raise StorageError(
                f"Supabase Storage is misconfigured: {exc}. Fix SUPABASE_URL and "
                "SUPABASE_SERVICE_KEY, or set STORAGE_BACKEND=none to run without "
                "retaining originals."
            ) from exc
    elif backend == "s3":
        try:
            storage = S3Storage()
        except Exception as exc:
            # Falling back to local on a hosted free tier would silently lose
            # files on the next deploy, so fail loudly instead.
            raise StorageError(
                f"Object storage is misconfigured: {exc}. Fix the S3_* settings, "
                "or set STORAGE_BACKEND=none to run without retaining originals."
            ) from exc
    else:
        storage = LocalStorage(settings.upload_dir)

    logger.info("File storage: %s (%s)", storage.name, storage.describe())
    return storage


def object_key(document_id: str, suffix: str) -> str:
    return f"documents/{document_id}{suffix}"
