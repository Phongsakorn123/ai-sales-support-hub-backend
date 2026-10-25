"""Download demo product images from Wikimedia Commons and assign them to products.

Usage (from the project root):
    python -m scripts.populate_product_images             # dry run: download, validate, report
    python -m scripts.populate_product_images --apply     # back up the DB, then set image_url

Only ``products.image_url`` is written, only where it is currently NULL, in one transaction.
Images are stored in ``static/products/`` (served by the app at ``/static/products/``) and the
source/licence of every image is recorded in ``static/products/credits.json``.
Requires macOS ``sips`` for decoding and resizing, so no image library is needed.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings

IMAGE_DIR = PROJECT_ROOT / "static" / "products"
CREDITS_PATH = IMAGE_DIR / "credits.json"
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
DEFAULT_BASE_URL = "http://localhost:8081"
URL_PATH = "/static/products"

API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AISalesSupportHubDemo/1.0 (local demo seed script)"
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024
MAX_OUTPUT_BYTES = 600 * 1024
TARGET_WIDTH = 800

# SKU -> Wikimedia Commons file. "exact" means the same model; "similar" means a closely
# related model or product type used because no photo of the exact model was available.
IMAGES: dict[str, tuple[str, str]] = {
    "MOCK-COM-001": ("File:ThinkPad T14.jpg", "exact"),
    "MOCK-COM-002": ("File:Dell OptiPlex 3060 SFF.jpg", "similar"),
    "MOCK-COM-003": ("File:HP Z820 Workstation.jpg", "similar"),
    "MOCK-COM-004": ("File:Intel NUC8.jpg", "similar"),
    "MOCK-COM-005": ("File:Dell PowerEdge servers.jpg", "similar"),
    "MOCK-NET-001": ("File:Cisco small business SG300-28 28-port Gigabit Ethernet rackmount switch.jpg", "similar"),
    "MOCK-NET-002": ("File:MikroTik hAP ac2.jpg", "similar"),
    "MOCK-NET-003": ("File:Access Point UniFi.jpg", "similar"),
    "MOCK-NET-004": ("File:Fortinet FortiGate 6501F.png", "similar"),
    "MOCK-NET-005": ("File:TP-Link UE300 ethernet USB adapter HS1.jpg", "exact"),
    "MOCK-PER-001": ("File:Logitech MX Keys YR0073 Wireless Keyboard.jpg", "similar"),
    "MOCK-PER-002": ("File:Logitech MX Master 3S HS01.jpg", "exact"),
    "MOCK-PER-003": ("File:3x Dell Ultrasharps.jpg", "similar"),
    "MOCK-PER-004": ("File:Lenovo ThinkPad Mini Dock Plus Series 3 with USB 3.0 (Type 4338) 01.jpg", "similar"),
    "MOCK-PER-005": ("File:Webcam (Logitech c922).jpg", "similar"),
    "MOCK-LAB-001": ("File:Rigol DP832 Power Supply Teardown SAM 9875 (9604552327).jpg", "exact"),
    "MOCK-LAB-002": ("File:RIGOL DS1054Z Digital Storage Oscilloscope.jpg", "exact"),
    "MOCK-LAB-003": ("File:Keysight U1461A Insulation Resistance Multimeter Teardown (27837790811).jpg", "similar"),
    "MOCK-LAB-004": ("File:20260422 Wrocław hackerspace testing led driver 12v PWM80 Rigol-DP832 Siglent-SDG2042X.webp", "exact"),
    "MOCK-LAB-005": ("File:Fluke 70 series II multimeter (connected) 01.jpg", "similar"),
    "MOCK-MON-001": ("File:AM2302 (DHT22) digital temperature and humidity sensor module.jpg", "similar"),
    "MOCK-MON-002": ("File:Overview of Netatmo Weather Interior module in Michle, Prague.jpg", "similar"),
    "MOCK-MON-003": ("File:Zabbix 4.0 web UI.png", "similar"),
    "MOCK-MON-004": ("File:APC AP9606 Web-SNMP Management Card.jpg", "similar"),
    "MOCK-MON-005": ("File:Bullet camera and Vandal Dome camera.jpg", "similar"),
    "MOCK-KBD-001": ("File:Keychron K8 Non-Backlight Wireless Mechanical Keyboard - 52772912798.jpg", "exact"),
    "MOCK-KBD-002": ("File:Razer BlackWidow Ultimate 2014 Elite Mechanical Gaming Keyboard.jpg", "similar"),
    "MOCK-KBD-003": ("File:DasKeyboard (1).jpg", "similar"),
    "MOCK-KBD-004": ("File:Apple Magic Keyboard - US.jpg", "exact"),
    "MOCK-KBD-005": ("File:Microsoft Sculpt ergonomic keyboard (1).jpg", "exact"),
    "MOCK-MSE-001": ("File:Razer DeathAdder Elite-front oblique-ar 16to10-fs PNr°0465.jpg", "exact"),
    "MOCK-MSE-002": ("File:Logitech-g502 (13971829039).jpg", "exact"),
    "MOCK-MSE-003": ("File:Magic Mouse.jpg", "exact"),
    "MOCK-MSE-004": ("File:Microsoft Pro IntelliMouse -- 1.jpg", "exact"),
    "MOCK-MSE-005": ("File:Rival Mouse by SteelSeries, FACEIT (2018, Cropped).jpg", "exact"),
    "MOCK-LPT-001": ("File:M2 Macbook Air Starlight model.jpg", "exact"),
    "MOCK-LPT-002": ("File:DELL XPS 13 and 15 (37080596413).jpg", "exact"),
    "MOCK-LPT-003": ("File:ASUS ZenBook Duo UX481FL.jpg", "similar"),
    "MOCK-LPT-004": ("File:Acer Swift 7 20201101a.jpg", "exact"),
    "MOCK-LPT-005": ("File:Microsoft Surface Laptop 7.jpg", "exact"),
    "MOCK-MNT-001": ("File:BenQ monitors (XL2420T XL2410T).jpg", "exact"),
    "MOCK-MNT-002": ("File:Asus ProArt PA246Q monitor 1.JPG", "exact"),
    "MOCK-MNT-003": ("File:Monitores led de aoc .jpg", "similar"),
    "MOCK-MNT-004": ("File:LG UltraFine 5K Display.jpg", "exact"),
    "MOCK-MNT-005": ("File:Dell U3415W monitor and Latitude laptop (1).jpg", "exact"),
    "MOCK-HDP-001": ("File:Bose QuietComfort 35 II Wireless Headphones.jpg", "exact"),
    "MOCK-HDP-002": ("File:Headphones-Sennheiser-HD555.jpg", "similar"),
    "MOCK-HDP-003": ("File:Sony MDR-7506 7615.jpg", "exact"),
    "MOCK-HDP-004": ("File:Audio Technica ATH-M50 Headphones.jpg", "exact"),
    "MOCK-HDP-005": ("File:JBL LIVE 650 BTNC (Over-Ear Noise Cancelling Headphone). (51127342181).jpg", "exact"),
}

SIGNATURES = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
)


def _request(url: str, *, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        time.sleep(1.5 * (attempt * 4 + 1))
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
            return data
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == attempts - 1:
                raise
    raise RuntimeError("unreachable")


def _image_kind(data: bytes) -> str | None:
    for signature, kind in SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def fetch_metadata(titles: list[str]) -> dict[str, dict]:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "format": "json",
            "titles": "|".join(titles),
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "iiurlwidth": 1000,
        }
    )
    data = json.loads(_request(f"{API}?{query}"))
    pages = data["query"]["pages"]
    # Commons normalizes titles (e.g. underscores), so map back through the normalization list.
    renamed = {item["from"]: item["to"] for item in data["query"].get("normalized", [])}
    by_title = {page["title"]: page for page in pages.values()}
    result = {}
    for title in titles:
        page = by_title.get(renamed.get(title, title))
        if page is None or "imageinfo" not in page:
            raise SystemExit(f"Commons file not found: {title}")
        info = page["imageinfo"][0]
        meta = info.get("extmetadata", {})

        def text(key: str) -> str:
            return re.sub(r"\s+", " ", re.sub("<[^>]+>", "", meta.get(key, {}).get("value", ""))).strip()

        result[title] = {
            "download_url": info.get("thumburl") or info["url"],
            "source_page": info["descriptionurl"],
            "author": text("Artist"),
            "license": text("LicenseShortName"),
            "license_url": text("LicenseUrl"),
        }
    return result


def _sips(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sips", *args], capture_output=True, text=True, timeout=60)


def decode_dimensions(path: Path) -> tuple[int, int]:
    result = _sips("-g", "pixelWidth", "-g", "pixelHeight", str(path))
    values = dict(re.findall(r"(pixelWidth|pixelHeight): (\d+)", result.stdout))
    if result.returncode != 0 or len(values) != 2:
        raise ValueError(f"{path.name} could not be decoded as an image")
    return int(values["pixelWidth"]), int(values["pixelHeight"])


def prepare_image(sku: str, entry: dict) -> Path:
    """Download (if needed), validate and convert one image to a resized JPEG."""
    target = IMAGE_DIR / f"{sku}.jpg"
    if target.exists():
        try:
            decode_dimensions(target)
            if target.read_bytes().startswith(b"\xff\xd8\xff"):
                return target
        except ValueError:
            pass  # existing file is invalid; fall through and rebuild it
        raise SystemExit(f"{target} exists but is not a valid JPEG; remove it manually to rebuild.")

    data = _request(entry["download_url"])
    kind = _image_kind(data)
    if kind is None:
        raise SystemExit(f"{sku}: downloaded content is not a JPEG, PNG or WebP image")
    source = IMAGE_DIR / f".{sku}.download.{kind}"
    source.write_bytes(data)
    try:
        decode_dimensions(source)
        result = _sips(
            "-s", "format", "jpeg", "-s", "formatOptions", "78", "-Z", str(TARGET_WIDTH),
            str(source), "--out", str(target),
        )
        if result.returncode != 0 or not target.exists():
            raise SystemExit(f"{sku}: conversion failed: {result.stderr.strip()[:200]}")
    finally:
        source.unlink(missing_ok=True)
    if target.stat().st_size > MAX_OUTPUT_BYTES or not target.read_bytes().startswith(b"\xff\xd8\xff"):
        size = target.stat().st_size
        target.unlink()
        raise SystemExit(f"{sku}: converted image invalid or too large ({size} bytes)")
    decode_dimensions(target)
    return target


def backup_database(database_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{database_path.stem}.pre-images-{datetime.now():%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    check = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"Backup integrity check failed: {backup_path}")
        backup_count = check.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    finally:
        check.close()
    live = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        live_count = live.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    finally:
        live.close()
    if backup_count != live_count:
        raise SystemExit("Backup product count does not match the live database")
    print(f"Backup verified: {backup_path} ({backup_count} products)")
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="back up and update image_url")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"public base URL that serves {URL_PATH} (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    database_path = settings.database_path
    print(f"Database: {database_path}")
    connection = sqlite3.connect(database_path)
    rows = {
        sku: (product_id, name, image_url)
        for product_id, sku, name, image_url in connection.execute(
            "SELECT id, sku, name, image_url FROM products WHERE sku LIKE 'MOCK-%'"
        )
    }
    missing = sorted(set(IMAGES) - set(rows))
    if missing:
        print(f"SKUs in the mapping but not in the database: {missing}", file=sys.stderr)

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    titles = [title for title, _ in IMAGES.values()]
    if len(set(titles)) != len(titles):
        raise SystemExit("Duplicate image in mapping")
    metadata = fetch_metadata(titles)

    credits = {}
    plan = []
    for sku, (title, match) in IMAGES.items():
        if sku not in rows:
            continue
        info = metadata[title]
        path = prepare_image(sku, info)
        width, height = decode_dimensions(path)
        url = f"{base_url}{URL_PATH}/{path.name}"
        credits[sku] = {
            "file": path.name, "commons_title": title, "match": match,
            "source_page": info["source_page"], "author": info["author"],
            "license": info["license"], "license_url": info["license_url"],
        }
        product_id, name, current = rows[sku]
        plan.append((product_id, sku, name, url, current, f"{width}x{height}", path.stat().st_size))
    CREDITS_PATH.write_text(json.dumps(credits, indent=2, ensure_ascii=False) + "\n")

    print("\nPlan:")
    for product_id, sku, name, url, current, size, nbytes in plan:
        state = "SKIP (already has image_url)" if current else "set"
        print(f"  [{product_id:>2}] {sku} {name[:42]:<42} {size:>9} {nbytes//1024:>4} KB  {state}")
        print(f"       {url}")

    to_update = [(url, product_id) for product_id, _, _, url, current, _, _ in plan if current is None]
    existing = [(sku, current) for _, sku, _, _, current, _, _ in plan if current is not None]
    if existing:
        print(f"\nNot overwritten (existing image_url): {existing}")
    print(f"\n{len(plan)} validated, {len(to_update)} to update.")
    if not args.apply:
        print("Dry run only; re-run with --apply to update the database.")
        return 0
    if not to_update:
        print("Nothing to update.")
        return 0

    backup_database(database_path)
    try:
        with connection:
            cursor = connection.executemany(
                "UPDATE products SET image_url = ? WHERE id = ? AND image_url IS NULL", to_update
            )
        print(f"Updated {cursor.rowcount if cursor.rowcount >= 0 else len(to_update)} product(s).")
    except sqlite3.Error as error:
        print(f"Update rolled back: {error}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
