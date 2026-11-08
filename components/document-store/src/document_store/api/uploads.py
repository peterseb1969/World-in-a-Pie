"""Reading an uploaded file within the install's upload limit."""

from fastapi import HTTPException, UploadFile

_CHUNK = 65536


async def read_upload(file: UploadFile) -> bytes:
    """The upload's bytes, refused with 413 once they pass WIP_MAX_UPLOAD_SIZE.

    Read in 64 KB chunks, so an oversized upload is refused when it crosses
    the limit instead of after it has been held whole in memory.
    """
    from ..main import settings as app_settings

    max_size = app_settings.MAX_UPLOAD_SIZE
    buffer = bytearray()
    while chunk := await file.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum upload size is {max_size // (1024 * 1024)}MB",
            )
    return bytes(buffer)
