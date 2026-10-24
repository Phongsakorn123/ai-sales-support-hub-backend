import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = ROOT / ".venv" / "bin" / "alembic"
PRODUCT_REVISION = "fa8837aed041"
USER_REVISION = "c826f22a32bf"


class UserMigrationTests(unittest.TestCase):
    def test_username_migration_preserves_existing_users_and_products(self) -> None:
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

            self.run_alembic(["upgrade", USER_REVISION], env)
            with sqlite3.connect(database_path) as connection:
                connection.executemany(
                    "INSERT INTO users (email, hashed_password) VALUES (?, ?)",
                    (
                        ("existing-1@example.com", "existing-password-hash-1"),
                        ("existing-2@example.com", "existing-password-hash-2"),
                        ("existing-3@example.com", "existing-password-hash-3"),
                    ),
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
                user_columns = {
                    row[1]: row
                    for row in connection.execute("PRAGMA table_info(users)")
                }
                self.assertIn("username", user_columns)
                self.assertEqual(user_columns["username"][3], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT id, username, email, hashed_password FROM users ORDER BY id"
                    ).fetchall(),
                    [
                        (1, "user-1", "existing-1@example.com", "existing-password-hash-1"),
                        (2, "user-2", "existing-2@example.com", "existing-password-hash-2"),
                        (3, "user-3", "existing-3@example.com", "existing-password-hash-3"),
                    ],
                )
                unique_indexes = connection.execute(
                    "PRAGMA index_list(users)"
                ).fetchall()
                username_is_unique = any(
                    index[2] == 1
                    and [
                        column[2]
                        for column in connection.execute(
                            f"PRAGMA index_info('{index[1]}')"
                        )
                    ]
                    == ["username"]
                    for index in unique_indexes
                )
                self.assertTrue(username_is_unique)
                for username, email in (
                    ("user-1", "new-email@example.com"),
                    ("new-user", "existing-1@example.com"),
                ):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO users (username, email, hashed_password) "
                            "VALUES (?, ?, ?)",
                            (username, email, "another-test-hash"),
                        )
                    connection.rollback()
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

            self.run_alembic(["downgrade", USER_REVISION], env)
            with sqlite3.connect(database_path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("users", tables)
                self.assertNotIn(
                    "username",
                    {
                        row[1]
                        for row in connection.execute("PRAGMA table_info(users)")
                    },
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT email, hashed_password FROM users ORDER BY id"
                    ).fetchall(),
                    [
                        ("existing-1@example.com", "existing-password-hash-1"),
                        ("existing-2@example.com", "existing-password-hash-2"),
                        ("existing-3@example.com", "existing-password-hash-3"),
                    ],
                )
                self.assertEqual(
                    connection.execute("SELECT name FROM products").fetchone(),
                    ("Existing product",),
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
