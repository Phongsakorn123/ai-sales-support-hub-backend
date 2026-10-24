import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = ROOT / ".venv" / "bin" / "alembic"
PRODUCT_REVISION = "fa8837aed041"


class UserMigrationTests(unittest.TestCase):
    def test_user_migration_preserves_existing_products(self) -> None:
        with tempfile.TemporaryDirectory(prefix="auth-migration-tests-") as temp_dir:
            database_path = Path(temp_dir) / "migration test.db"
            env = os.environ.copy()
            env["DATABASE_PATH"] = str(database_path)

            self.run_alembic(["upgrade", PRODUCT_REVISION], env)
            with sqlite3.connect(database_path) as connection:
                product_schema_before = connection.execute(
                    "PRAGMA table_info(products)"
                ).fetchall()
                connection.execute(
                    "INSERT INTO products (name, price) VALUES (?, ?)",
                    ("Existing product", "42.50"),
                )
                connection.commit()

            self.run_alembic(["upgrade", "head"], env)
            with sqlite3.connect(database_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("products", tables)
                self.assertIn("users", tables)
                self.assertEqual(
                    connection.execute("PRAGMA table_info(products)").fetchall(),
                    product_schema_before,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT name, price FROM products"
                    ).fetchone(),
                    ("Existing product", 42.5),
                )

            self.run_alembic(["downgrade", PRODUCT_REVISION], env)
            with sqlite3.connect(database_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("products", tables)
                self.assertNotIn("users", tables)
                self.assertEqual(
                    connection.execute("SELECT name FROM products").fetchone(),
                    ("Existing product",),
                )

            self.assertTrue(database_path.exists())
        self.assertFalse(database_path.exists())

    def run_alembic(self, arguments: list[str], env: dict[str, str]) -> None:
        result = subprocess.run(
            [str(ALEMBIC), *arguments],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"alembic {' '.join(arguments)} failed:\n{result.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
