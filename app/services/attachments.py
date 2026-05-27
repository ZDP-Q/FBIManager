from __future__ import annotations

import logging
from typing import Any

from app.repositories import has_attachment, insert_comment_attachment


async def download_comment_attachments(
    comment: dict[str, Any],
    facebook: Any,
) -> int:
    """Download attachments for a comment and store as BLOB. Returns count saved."""
    comment_id = str(comment.get("id", ""))
    if not comment_id:
        return 0

    info = facebook.extract_attachment_info(comment)
    if not info:
        return 0

    media_type, url = info
    if not url:
        return 0

    if has_attachment(comment_id):
        return 0

    data = await facebook.download_attachment_bytes(url)
    if data:
        data = _compress_attachment(data, media_type)
        insert_comment_attachment(comment_id, media_type, url, data)
        return 1
    return 0


def _compress_attachment(data: bytes, media_type: str) -> bytes:
    """Compress image attachments to WebP. GIF and non-image types pass through."""
    compressible = {"sticker", "photo", "animated_image_share"}
    if media_type not in compressible:
        return data

    # Keep GIFs as-is (Pillow WebP animation support is unreliable)
    if len(data) >= 3 and data[:3] == b"GIF":
        return data

    try:
        from io import BytesIO
        from PIL import Image

        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        out = BytesIO()
        img.save(out, format="WEBP", quality=80)
        compressed = out.getvalue()
        # Only use compressed version if it's actually smaller
        return compressed if len(compressed) < len(data) else data
    except Exception as exc:
        logging.getLogger("uvicorn.error").warning(
            "[attachments] compress failed for %s: %s", media_type, exc
        )
        return data