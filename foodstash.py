"""Volume-backed cellar inventory with no third-party runtime dependencies."""

import datetime as dt
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
LOG = logging.getLogger("foodstash")


class ValidationError(ValueError):
    pass


class LookupUnavailable(Exception):
    pass


def valid_image_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    return bool(re.fullmatch(
        r"https://images\.openfoodfacts\.org/images/products/[0-9/]+/[a-zA-Z0-9_.-]+\.jpg",
        value,
    ))


def product_image(product):
    selected = product.get("selected_images")
    front = selected.get("front") if isinstance(selected, dict) else None
    display = front.get("display") if isinstance(front, dict) else None
    candidates = []
    if isinstance(display, dict):
        candidates.extend(display.get(language) for language in ("de", "fr", "it"))
    candidates.extend(product.get(field) for field in ("image_front_url", "image_url"))
    return next((url for url in candidates if valid_image_url(url)), "")


def validate_item(data):
    if not isinstance(data, dict):
        raise ValidationError("Enter a product name, expiry date and quantity.")
    if set(data) - {"name", "barcode", "expires_on", "quantity", "location", "notes", "image_url"}:
        raise ValidationError("Unknown inventory field.")
    result = {}
    for key, limit in (("name", 160), ("barcode", 32), ("location", 80), ("notes", 500)):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise ValidationError(f"{key.capitalize()} must be text of at most {limit} characters.")
        result[key] = value.strip()
    if not result["name"]:
        raise ValidationError("Enter a product name.")
    if result["barcode"] and not re.fullmatch(r"[0-9]{4,32}", result["barcode"]):
        raise ValidationError("Barcode must contain 4-32 digits, or be empty for manual entry.")
    image = data.get("image_url", "")
    if image != "" and (not valid_image_url(image) or not result["barcode"]):
        raise ValidationError("Product image must be an Open Food Facts image with a barcode.")
    result["image_url"] = image
    expires = data.get("expires_on")
    if not isinstance(expires, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expires):
        raise ValidationError("Choose an expiry date in YYYY-MM-DD format.")
    try:
        dt.date.fromisoformat(expires)
    except ValueError as exc:
        raise ValidationError("Choose a valid expiry date.") from exc
    quantity = data.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= 9999:
        raise ValidationError("Quantity must be a whole number from 1 to 9999.")
    result.update(expires_on=expires, quantity=quantity)
    return result


class Inventory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL, barcode TEXT NOT NULL,
                    expires_on TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 9999),
                    location TEXT NOT NULL, notes TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(items)")}
            if "image_url" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM items ORDER BY expires_on, name COLLATE NOCASE, id")]

    def add(self, data):
        item = validate_item(data)
        with self.connect() as db:
            cursor = db.execute("""
                INSERT INTO items (name,barcode,expires_on,quantity,location,notes,image_url)
                VALUES (:name,:barcode,:expires_on,:quantity,:location,:notes,:image_url)
            """, item)
            return dict(db.execute("SELECT * FROM items WHERE id=?", (cursor.lastrowid,)).fetchone())

    def update(self, item_id, data):
        item = validate_item(data)
        item["id"] = item_id
        with self.connect() as db:
            cursor = db.execute("""
                UPDATE items SET name=:name,barcode=:barcode,expires_on=:expires_on,
                quantity=:quantity,location=:location,notes=:notes,image_url=:image_url WHERE id=:id
            """, item)
            return bool(cursor.rowcount)

    def delete(self, item_id):
        with self.connect() as db:
            return bool(db.execute("DELETE FROM items WHERE id=?", (item_id,)).rowcount)

    def consume(self, item_id):
        with self.connect() as db:
            # Lock before reading, so two requests cannot consume the same last unit.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT quantity FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return False
            if row["quantity"] == 1:
                db.execute("DELETE FROM items WHERE id=?", (item_id,))
            else:
                db.execute("UPDATE items SET quantity=quantity-1 WHERE id=?", (item_id,))
            return True


def lookup_product(barcode):
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{12,14})", barcode):
        raise ValidationError("Lookup supports 8, 12, 13 or 14 digit food barcodes. Other products can be entered manually.")
    request = Request(
        f"https://ch.openfoodfacts.org/api/v2/product/{barcode}.json"
        "?cc=ch&lc=de&fields=code,product_name,product_name_de,product_name_fr,"
        "product_name_it,product_name_en,brands,quantity,selected_images,image_front_url,image_url",
        headers={"User-Agent": f"FoodStash/{VERSION} (https://github.com/Lela810/FoodStash)"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read(256_001)
        if len(raw) > 256_000:
            raise ValueError("Oversized product response")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Invalid product response")
        if payload.get("status") == 0:
            return None
        if payload.get("status") != 1 or not isinstance(payload.get("product"), dict):
            raise ValueError("Invalid product response")
        product = payload["product"]
        code = product.get("code")
        # UPC/EAN representations can differ only by leading zero padding.
        if code is not None and (not isinstance(code, str) or not code.isdigit()
                                 or code.zfill(14) != barcode.zfill(14)):
            raise ValueError("Product response does not match the scanned barcode")
        name = next((product.get(field) for field in
                     ("product_name_de", "product_name_fr", "product_name_it", "product_name", "product_name_en")
                     if isinstance(product.get(field), str) and product[field].strip()), None)
        if not isinstance(name, str) or not name.strip():
            return None
        return {"barcode": barcode, "name": name.strip()[:160],
                "image_url": product_image(product),
                "brand": product.get("brands") if isinstance(product.get("brands"), str) else "",
                "size": product.get("quantity") if isinstance(product.get("quantity"), str) else ""}
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            return None
        LOG.warning("Product lookup HTTP error: %s", exc.code)
        raise LookupUnavailable("Product lookup is unavailable. Enter the product manually or retry.") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        LOG.warning("Product lookup failed: %s", exc)
        raise LookupUnavailable("Could not read the product database. Enter the product manually or retry.") from exc


def make_handler(inventory):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FoodStash"

        def send_json(self, status, data):
            self.send_bytes(status, json.dumps(data).encode(), "application/json; charset=utf-8")

        def send_bytes(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; script-src 'self'; "
                             "img-src 'self' https://images.openfoodfacts.org; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ("http", "https") or parsed.netloc != self.headers.get("Host"):
                    raise ValidationError("Cross-origin inventory changes are not allowed.")
            if self.headers.get_content_type() != "application/json":
                raise ValidationError("Request must use application/json.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValidationError("Invalid request length.") from exc
            if not 0 < length <= 16_384:
                raise ValidationError("Request body must be between 1 and 16384 bytes.")
            try:
                return json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError) as exc:
                raise ValidationError("Invalid JSON request.") from exc

        def dispatch(self):
            path = urlsplit(self.path).path
            if self.command == "GET":
                if path == "/api/health":
                    inventory.list()
                    return self.send_json(200, {"status": "ok", "version": VERSION})
                if path == "/api/items":
                    return self.send_json(200, {"items": inventory.list()})
                if path.startswith("/api/products/"):
                    return self.send_json(200, {"product": lookup_product(path.removeprefix("/api/products/"))})
                assets = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                          "/styles.css": ("styles.css", "text/css; charset=utf-8")}
                if path in assets:
                    filename, content_type = assets[path]
                    return self.send_bytes(200, (ROOT / "static" / filename).read_bytes(), content_type)
            elif self.command in ("POST", "PATCH", "DELETE"):
                data = self.read_json()
                if path == "/api/items" and self.command == "POST":
                    return self.send_json(201, {"item": inventory.add(data)})
                match = re.fullmatch(r"/api/items/([1-9][0-9]{0,17})(/consume)?", path)
                if match:
                    item_id = int(match[1])
                    result = None
                    if match[2] and self.command == "POST":
                        result = inventory.consume(item_id)
                    elif not match[2] and self.command == "DELETE":
                        result = inventory.delete(item_id)
                    elif not match[2] and self.command == "PATCH":
                        result = inventory.update(item_id, data)
                    if result is not None:
                        return self.send_json(200 if result else 404,
                                              {"ok": True} if result else {"error": "Batch no longer exists. Refresh the inventory."})
            self.send_json(404, {"error": "Not found."})

        def handle_request(self):
            try:
                self.dispatch()
            except ValidationError as exc:
                self.send_json(400, {"error": str(exc)})
            except LookupUnavailable as exc:
                self.send_json(502, {"error": str(exc)})
            except (sqlite3.Error, OSError):
                LOG.exception("Inventory request failed")
                self.send_json(503, {"error": "Inventory is unavailable. Check storage and server logs."})

        do_GET = handle_request
        do_POST = handle_request
        do_PATCH = handle_request
        do_DELETE = handle_request

        def log_message(self, fmt, *args):
            LOG.info(fmt, *args)

    return Handler


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    inventory = Inventory(os.environ.get("FOODSTASH_DB", str(ROOT / "data" / "foodstash.db")))
    server = ThreadingHTTPServer(
        (os.environ.get("FOODSTASH_HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080"))),
        make_handler(inventory),
    )
    LOG.info("FoodStash %s listening on %s:%s", VERSION, *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
