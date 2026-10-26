import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

PRODUCT_IMAGES = Path(__file__).resolve().parents[2] / "static" / "products"


class StaticProductImageTests(unittest.TestCase):
    def test_product_images_are_served_as_jpeg(self):
        images = sorted(PRODUCT_IMAGES.glob("MOCK-*.jpg"))
        self.assertTrue(images)
        with TestClient(app) as client:
            for image in images:
                response = client.get(f"/static/products/{image.name}")
                self.assertEqual(response.status_code, 200, image.name)
                self.assertEqual(response.headers["content-type"], "image/jpeg")
                self.assertEqual(response.content[:3], b"\xff\xd8\xff")

    def test_missing_image_returns_404(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/static/products/nope.jpg").status_code, 404)


if __name__ == "__main__":
    unittest.main()
