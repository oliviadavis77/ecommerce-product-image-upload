import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from product_image_upload import product_image_key, resized_jpeg


class ProductImageUploadTest(unittest.TestCase):
    def test_resize_keeps_the_product_frame_and_builds_a_stable_key(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "original.png"
            Image.new("RGB", (2000, 1000), color="white").save(source)
            output = Image.open(__import__("io").BytesIO(resized_jpeg(source, 1000)))

        self.assertEqual(output.size, (1000, 500))
        self.assertEqual(product_image_key("linen-shirt", 1000), "products/linen-shirt/display-1000.jpg")


if __name__ == "__main__":
    unittest.main()
