"""S3-compatible storage client for file operations with MinIO."""

import os
from contextlib import asynccontextmanager
from typing import Any, cast

from aiobotocore.session import get_session
from botocore.config import Config
from botocore.exceptions import ClientError

# Storage was the only outbound dependency in this service with no timeout of
# its own: botocore defaults to 60s connect AND 60s read, with retry_mode
# resolving to `legacy` and max_attempts unset, so a MinIO that accepts
# connections but stops answering stalls a caller for minutes. Every other
# client here (registry, def-store, template-store) passes an explicit
# timeout; these two configs bring storage in line.
#
# read_timeout is per socket read, NOT per request — botocore's own wording is
# "the time till a timeout exception is thrown when attempting to read from a
# connection". A streaming 850 MB archive upload is therefore never at risk
# from this value while bytes keep moving; only a genuinely stalled socket
# trips it. That is what lets one config cover every data operation.
_DATA_CONFIG = Config(
    connect_timeout=5,
    read_timeout=60,
    retries={"max_attempts": 3, "mode": "standard"},
)

# The health path gets a tighter budget than the data path: it exists to answer
# a caller that is already timing it, so waiting out a data-sized timeout only
# converts "storage is down" into "the caller is down too". max_attempts=1 in
# standard mode means one attempt total, no retries — a probe retries by being
# a probe.
#
# connect_timeout is 1, not the 2 that looks natural for a "5s budget", because
# the wall time is NOT the configured timeout. Measured against a blackholed
# endpoint (packets dropped, so a connect hangs to its limit), the client makes
# two connection attempts per botocore attempt and adds ~0.9s of fixed setup:
#
#     connect_timeout=1 -> 2.83s     connect_timeout=2 -> 4.58s
#     connect_timeout=3 -> 6.42s     i.e. wall ~= 2 * connect_timeout + 0.9
#
# So 2 would land at 4.6s against a 5s probe budget — inside it, but with no
# usable margin. 1 lands at ~2.8s. A healthy MinIO answers in 6-41ms, so a 1s
# connect ceiling is ~25x the observed p99 and will not cause false negatives.
_HEALTH_CONFIG = Config(
    connect_timeout=1,
    read_timeout=2,
    retries={"max_attempts": 1, "mode": "standard"},
)


class FileStorageClient:
    """
    S3-compatible storage client for MinIO/AWS S3.

    Handles low-level storage operations:
    - Upload files to storage
    - Download files from storage
    - Delete files from storage
    - Generate pre-signed URLs for direct download
    - Check if files exist
    """

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        region: str = "us-east-1",
        public_endpoint_url: str | None = None,
    ):
        """
        Initialize the file storage client.

        Args:
            endpoint_url: S3 endpoint URL (default from env WIP_FILE_STORAGE_ENDPOINT)
            access_key: Access key (default from env WIP_FILE_STORAGE_ACCESS_KEY)
            secret_key: Secret key (default from env WIP_FILE_STORAGE_SECRET_KEY)
            bucket: Bucket name (default from env WIP_FILE_STORAGE_BUCKET)
            region: AWS region (only used for signature, not relevant for MinIO)
            public_endpoint_url: Browser-accessible endpoint for pre-signed URLs
                (default from env WIP_FILE_STORAGE_PUBLIC_ENDPOINT, falls back to
                http://localhost:9000). Required when the internal endpoint uses a
                container hostname (e.g. http://wip-minio:9000) that browsers cannot resolve.
        """
        self.endpoint_url = endpoint_url or os.getenv(
            "WIP_FILE_STORAGE_ENDPOINT",
            "http://localhost:9000"
        )
        self.public_endpoint_url = public_endpoint_url or os.getenv(
            "WIP_FILE_STORAGE_PUBLIC_ENDPOINT",
            "http://localhost:9000"
        )
        self.access_key = access_key or os.getenv(
            "WIP_FILE_STORAGE_ACCESS_KEY",
            "wip-minio-root"
        )
        self.secret_key = secret_key or os.getenv(
            "WIP_FILE_STORAGE_SECRET_KEY",
            "wip-minio-password"
        )
        self.bucket = bucket or os.getenv(
            "WIP_FILE_STORAGE_BUCKET",
            "wip-attachments"
        )
        self.region = region
        self._session = get_session()

    @asynccontextmanager
    async def _get_client(self, config: Config | None = None):
        """Get an async S3 client.

        Defaults to the data-operation timeouts; callers whose latency budget
        is smaller than a data transfer's (the health check) pass their own.
        """
        async with self._session.create_client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=config or _DATA_CONFIG,
        ) as client:
            yield client

    async def upload(
        self,
        storage_key: str,
        content: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None
    ) -> None:
        """
        Upload a file to storage.

        Args:
            storage_key: Key to store the file under (typically file_id)
            content: File content as bytes
            content_type: MIME type of the file
            metadata: Optional metadata to store with the file

        Raises:
            FileStorageError: If upload fails
        """
        try:
            async with self._get_client() as client:
                extra_args: dict[str, Any] = {
                    "ContentType": content_type,
                }
                if metadata:
                    extra_args["Metadata"] = metadata

                await client.put_object(
                    Bucket=self.bucket,
                    Key=storage_key,
                    Body=content,
                    **extra_args
                )
        except ClientError as e:
            raise FileStorageError(f"Failed to upload file: {e}") from e

    async def download(self, storage_key: str) -> bytes:
        """
        Download a file from storage (loads entire file into memory).

        Args:
            storage_key: Key of the file to download

        Returns:
            File content as bytes

        Raises:
            FileStorageError: If download fails or file not found
        """
        try:
            async with self._get_client() as client:
                response = await client.get_object(
                    Bucket=self.bucket,
                    Key=storage_key
                )
                async with response["Body"] as stream:
                    return cast(bytes, await stream.read())
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise FileStorageError(f"File not found: {storage_key}") from e
            raise FileStorageError(f"Failed to download file: {e}") from e

    async def download_stream(self, storage_key: str, chunk_size: int = 64 * 1024):
        """
        Stream a file from storage in chunks.

        Yields chunks without buffering the entire file in memory.

        Args:
            storage_key: Key of the file to download
            chunk_size: Size of each chunk in bytes (default: 64 KB)

        Yields:
            File content chunks as bytes

        Raises:
            FileStorageError: If download fails or file not found
        """
        try:
            async with self._get_client() as client:
                response = await client.get_object(
                    Bucket=self.bucket,
                    Key=storage_key
                )
                # Use the StreamingBody wrapper directly — NOT `async with
                # response["Body"] as stream` which yields the raw aiohttp
                # ClientResponse whose read() takes no size argument.
                body = response["Body"]
                try:
                    while True:
                        chunk = await body.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    body.close()
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise FileStorageError(f"File not found: {storage_key}") from e
            raise FileStorageError(f"Failed to download file: {e}") from e

    async def upload_file(
        self,
        storage_key: str,
        file_path: str,
        content_type: str = "application/zip",
    ) -> None:
        """
        Upload a local file to storage without buffering it in memory.

        The open file handle is passed as the request body — botocore
        streams a seekable body in small reads (the reads themselves are
        sync disk I/O interleaved with the async send; acceptable for
        archive-sized files, unlike loading multi-GB into RAM).

        Args:
            storage_key: Key to store the file under
            file_path: Local filesystem path of the file to upload
            content_type: MIME type (archives default to application/zip)

        Raises:
            FileStorageError: If upload fails
        """
        try:
            async with self._get_client() as client:
                with open(file_path, "rb") as fh:
                    await client.put_object(
                        Bucket=self.bucket,
                        Key=storage_key,
                        Body=fh,
                        ContentType=content_type,
                    )
        except (ClientError, OSError) as e:
            raise FileStorageError(f"Failed to upload file {file_path}: {e}") from e

    async def download_to_file(
        self,
        storage_key: str,
        dest_path: str,
        chunk_size: int = 1024 * 1024,
    ) -> int:
        """
        Stream an object from storage into a local file.

        Args:
            storage_key: Key of the object to download
            dest_path: Local filesystem path to write
            chunk_size: Read size per chunk

        Returns:
            Bytes written.

        Raises:
            FileStorageError: If download fails or object not found
        """
        written = 0
        try:
            with open(dest_path, "wb") as fh:
                async for chunk in self.download_stream(storage_key, chunk_size):
                    fh.write(chunk)
                    written += len(chunk)
            return written
        except (FileStorageError, OSError):
            # Never leave a partial file behind masquerading as an archive.
            import contextlib
            import os as _os
            with contextlib.suppress(OSError):
                _os.unlink(dest_path)
            raise

    async def copy_object(self, source_key: str, dest_key: str) -> None:
        """
        Server-side copy within the bucket — no bytes travel through this
        process, so duplicating a multi-GB archive is near-free.

        Args:
            source_key: Existing object key
            dest_key: Key for the copy

        Raises:
            FileStorageError: If the copy fails or the source is missing
        """
        try:
            async with self._get_client() as client:
                await client.copy_object(
                    Bucket=self.bucket,
                    Key=dest_key,
                    CopySource={"Bucket": self.bucket, "Key": source_key},
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise FileStorageError(f"File not found: {source_key}") from e
            raise FileStorageError(f"Failed to copy object: {e}") from e

    async def delete(self, storage_key: str) -> None:
        """
        Delete a file from storage.

        Args:
            storage_key: Key of the file to delete

        Raises:
            FileStorageError: If deletion fails
        """
        try:
            async with self._get_client() as client:
                await client.delete_object(
                    Bucket=self.bucket,
                    Key=storage_key
                )
        except ClientError as e:
            raise FileStorageError(f"Failed to delete file: {e}") from e

    async def exists(self, storage_key: str) -> bool:
        """
        Check if a file exists in storage.

        Args:
            storage_key: Key of the file to check

        Returns:
            True if file exists, False otherwise
        """
        try:
            async with self._get_client() as client:
                await client.head_object(
                    Bucket=self.bucket,
                    Key=storage_key
                )
                return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return False
            raise FileStorageError(f"Failed to check file existence: {e}") from e

    async def generate_download_url(
        self,
        storage_key: str,
        expires_in: int = 3600,
        filename: str | None = None
    ) -> str:
        """
        Generate a pre-signed URL for direct download.

        Args:
            storage_key: Key of the file
            expires_in: URL expiration time in seconds (default: 1 hour)
            filename: Optional filename for Content-Disposition header

        Returns:
            Pre-signed URL for download

        Raises:
            FileStorageError: If URL generation fails
        """
        try:
            async with self._get_client() as client:
                params = {
                    "Bucket": self.bucket,
                    "Key": storage_key,
                }
                if filename:
                    params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'

                url = await client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=expires_in
                )

                # Rewrite internal endpoint to public endpoint for browser access.
                # The S3 client generates URLs using the internal endpoint (e.g.
                # http://wip-minio:9000) which browsers cannot resolve.
                if self.public_endpoint_url and self.public_endpoint_url != self.endpoint_url:
                    url = url.replace(self.endpoint_url, self.public_endpoint_url, 1)

                return cast(str, url)
        except ClientError as e:
            raise FileStorageError(f"Failed to generate download URL: {e}") from e

    async def get_file_info(self, storage_key: str) -> dict:
        """
        Get metadata about a file in storage.

        Args:
            storage_key: Key of the file

        Returns:
            Dict with file info (size, content_type, last_modified, metadata)

        Raises:
            FileStorageError: If file not found or request fails
        """
        try:
            async with self._get_client() as client:
                response = await client.head_object(
                    Bucket=self.bucket,
                    Key=storage_key
                )
                return {
                    "size_bytes": response.get("ContentLength", 0),
                    "content_type": response.get("ContentType", "application/octet-stream"),
                    "last_modified": response.get("LastModified"),
                    "metadata": response.get("Metadata", {}),
                }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                raise FileStorageError(f"File not found: {storage_key}") from e
            raise FileStorageError(f"Failed to get file info: {e}") from e

    async def health_check(self) -> bool:
        """Check if the storage service is healthy."""
        try:
            async with self._get_client(_HEALTH_CONFIG) as client:
                # Try to list buckets as a health check
                await client.list_buckets()
                return True
        except Exception:
            return False

    async def ensure_bucket_exists(self) -> bool:
        """
        Ensure the configured bucket exists, create if not.

        Returns:
            True if bucket exists or was created

        Raises:
            FileStorageError: If bucket creation fails
        """
        try:
            async with self._get_client() as client:
                try:
                    await client.head_bucket(Bucket=self.bucket)
                    return True
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code == "404":
                        # Bucket doesn't exist, create it
                        await client.create_bucket(Bucket=self.bucket)
                        return True
                    raise
        except ClientError as e:
            raise FileStorageError(f"Failed to ensure bucket exists: {e}") from e


class FileStorageError(Exception):
    """Error with file storage operations."""
    pass


# Singleton instance
_client: FileStorageClient | None = None


def get_file_storage_client() -> FileStorageClient:
    """Get the singleton file storage client instance."""
    global _client
    if _client is None:
        _client = FileStorageClient()
    return _client


def configure_file_storage_client(
    endpoint_url: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    bucket: str | None = None,
) -> FileStorageClient:
    """Configure and return the file storage client."""
    global _client
    _client = FileStorageClient(
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
    )
    return _client


def is_file_storage_enabled() -> bool:
    """Check if file storage is enabled via environment."""
    return os.getenv("WIP_FILE_STORAGE_ENABLED", "false").lower() == "true"
