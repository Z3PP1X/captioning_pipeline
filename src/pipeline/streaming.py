"""HTTP 206 Partial Content byte-range video streaming for smooth browser scrubbing."""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Generator

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def file_chunk_generator(path: Path, start: int, length: int, chunk_size: int = 1024 * 512) -> Generator[bytes, None, None]:
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            read_size = min(chunk_size, remaining)
            chunk = f.read(read_size)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_video(path: Path, request: Request) -> Response:
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Video file not found")

    file_size = os.path.getsize(path)
    content_type, _ = mimetypes.guess_type(path.name)
    if not content_type:
        content_type = "video/mp4"

    range_header = request.headers.get("range")
    if not range_header:
        # Normal 200 response
        return StreamingResponse(
            file_chunk_generator(path, 0, file_size),
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    match = RANGE_RE.match(range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid Range header format")

    start_str, end_str = match.groups()

    if not start_str and not end_str:
        raise HTTPException(status_code=416, detail="Invalid Range values")

    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    else:  # Suffix byte range
        length = int(end_str)
        start = max(0, file_size - length)
        end = file_size - 1

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": content_type,
    }

    return StreamingResponse(
        file_chunk_generator(path, start, content_length),
        status_code=206,
        headers=headers,
        media_type=content_type,
    )
