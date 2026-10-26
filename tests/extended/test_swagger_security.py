import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import SWAGGER_CSS_SRI, SWAGGER_JS_SRI, app

STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "swagger-ui"


class SwaggerSecurityTests(unittest.TestCase):
    def test_docs_use_local_integrity_checked_assets_and_a_matching_nonce(self):
        with TestClient(app) as client:
            response = client.get("/docs")
            self.assertEqual(response.status_code, 200)
            html = response.text
            csp = response.headers["content-security-policy"]

            self.assertIn('href="/static/swagger-ui/swagger-ui.css"', html)
            self.assertIn('src="/static/swagger-ui/swagger-ui-bundle.js"', html)
            self.assertIn(f'integrity="{SWAGGER_CSS_SRI}"', html)
            self.assertIn(f'integrity="{SWAGGER_JS_SRI}"', html)
            self.assertNotIn("cdn.jsdelivr.net", html)
            self.assertNotIn("cdn.jsdelivr.net", csp)

            nonce_match = re.search(r"'nonce-([^']+)'", csp)
            self.assertIsNotNone(nonce_match)
            self.assertTrue(re.findall(r'<script nonce="([^"]+)"', html))
            self.assertTrue(
                all(
                    value == nonce_match.group(1)
                    for value in re.findall(r'<script nonce="([^"]+)"', html)
                )
            )

            self.assertEqual(
                client.get("/static/swagger-ui/swagger-ui.css").status_code, 200
            )
            self.assertEqual(
                client.get("/static/swagger-ui/swagger-ui-bundle.js").status_code, 200
            )

    def test_vendored_assets_match_the_integrity_hashes(self):
        import base64
        import hashlib

        for filename, expected in (
            ("swagger-ui.css", SWAGGER_CSS_SRI),
            ("swagger-ui-bundle.js", SWAGGER_JS_SRI),
        ):
            digest = base64.b64encode(
                hashlib.sha384((STATIC_DIR / filename).read_bytes()).digest()
            ).decode()
            self.assertEqual(expected, f"sha384-{digest}")


if __name__ == "__main__":
    unittest.main()
