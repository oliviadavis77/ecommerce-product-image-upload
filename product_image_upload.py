"""Resize a product photo and write the display image to Infrai object storage."""

from __future__ import annotations

import argparse
import io
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from infrai_media_storage import Infrai, ensure_bucket


DEFAULT_BUCKET = "catalog-media"


def resized_jpeg(source: Path, width: int) -> bytes:
    with Image.open(source) as image:
        image = image.convert("RGB")
        height = round(image.height * width / image.width)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)
        return output.getvalue()


def product_image_key(product_id: str, width: int) -> str:
    return f"products/{product_id}/display-{width}.jpg"


def _retry_delay(headers: object, attempt: int) -> float:
    value = getattr(headers, "get", lambda _name: None)("Retry-After")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    return 0.5 * (2**attempt)


def upload_signed(url: str, image_bytes: bytes) -> None:
    for attempt in range(4):
        request = Request(
            url,
            data=image_bytes,
            method="PUT",
            headers={"Content-Type": "image/jpeg"},
        )
        try:
            with urlopen(request):
                return
        except HTTPError as error:
            if error.code == 429 and attempt < 3:
                time.sleep(_retry_delay(error.headers, attempt))
                continue
            raise RuntimeError(f"Upload returned HTTP {error.code}") from error
    raise RuntimeError("Upload retry limit reached")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resize and store a product image.")
    parser.add_argument("source", type=Path, help="Original product image")
    parser.add_argument("--product-id", required=True, help="Stable catalog product identifier")
    parser.add_argument("--width", type=int, default=1200, help="Output width in pixels")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Object storage bucket")
    args = parser.parse_args()

    if args.width < 1:
        parser.error("--width must be positive")

    infrai = Infrai()
    ensure_bucket(infrai, args.bucket)
    key = product_image_key(args.product_id, args.width)
    signed = infrai.storage.object.presign("put", args.bucket, key, 600)
    upload_signed(signed["url"], resized_jpeg(args.source, args.width))
    print(f"Stored {key}")


if __name__ == "__main__":
    main()
