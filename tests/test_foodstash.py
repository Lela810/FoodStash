import concurrent.futures
import io
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import foodstash


def item(**overrides):
    return {"name": "Chopped tomatoes", "barcode": "0001234567890",
            "expires_on": "2027-01-30", "quantity": 2, "location": "Shelf 2",
            "notes": "", **overrides}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "inventory.db"
        self.stock = foodstash.Inventory(self.path)

    def test_separate_batches_and_expiry_order(self):
        first = self.stock.add(item())
        second = self.stock.add(item(expires_on="2026-12-10"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual([second["id"], first["id"]], [i["id"] for i in self.stock.list()])
        self.assertEqual("0001234567890", first["barcode"])

    def test_persistence_after_new_instance(self):
        self.stock.add(item(name="A <script> is plain text"))
        self.assertEqual(self.stock.list(), foodstash.Inventory(self.path).list())

    def test_edit_consume_delete(self):
        key = self.stock.add(item())["id"]
        self.assertTrue(self.stock.update(key, item(name="Beans", quantity=2)))
        self.assertEqual("Beans", self.stock.list()[0]["name"])
        self.assertTrue(self.stock.consume(key))
        self.assertEqual(1, self.stock.list()[0]["quantity"])
        self.assertTrue(self.stock.consume(key))
        self.assertFalse(self.stock.consume(key))
        key = self.stock.add(item())["id"]
        self.assertTrue(self.stock.delete(key))
        self.assertFalse(self.stock.delete(key))
        self.assertFalse(self.stock.update(key, item()))

    def test_concurrent_consumption(self):
        key = self.stock.add(item(quantity=1))["id"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.stock.consume, [key, key]))
        self.assertEqual([False, True], sorted(results))
        self.assertEqual([], self.stock.list())

    def test_manual_product_without_barcode(self):
        self.assertEqual("", self.stock.add(item(barcode=""))["barcode"])

    def test_image_persistence_and_edit(self):
        image = "https://images.openfoodfacts.org/images/products/761/234/567/8901/front_de.3.400.jpg"
        row = self.stock.add(item(image_url=image))
        self.assertEqual(image, foodstash.Inventory(self.path).list()[0]["image_url"])
        self.stock.update(row["id"], item(image_url=image, quantity=3))
        self.assertEqual(image, self.stock.list()[0]["image_url"])
        self.stock.update(row["id"], item(barcode=""))
        self.assertEqual("", self.stock.list()[0]["image_url"])

    def test_existing_database_migration_preserves_stock(self):
        legacy = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY,name TEXT,barcode TEXT,"
                       "expires_on TEXT,quantity INTEGER,location TEXT,notes TEXT,created_at TEXT)")
            db.execute("INSERT INTO items VALUES (42,'Rice','12345678','2027-01-01',3,'Cellar','','now')")
        db.close()
        migrated = foodstash.Inventory(legacy)
        row = migrated.list()[0]
        self.assertEqual((42, "Rice", 3, ""), (row["id"], row["name"], row["quantity"], row["image_url"]))
        self.assertEqual(migrated.list(), foodstash.Inventory(legacy).list())

    def test_invalid_inputs_never_change_inventory(self):
        invalid = [item(name=" "), item(quantity=0), item(quantity=10000), item(quantity=True),
                   item(quantity=1.5), item(expires_on="2026-02-30"), item(expires_on="2026-2-2"),
                   item(barcode="../foo"), item(name=None), item(notes="x" * 501),
                   item(unexpected="field"), item(image_url=None),
                   item(image_url="https://evil.example/photo.jpg"),
                   item(barcode="", image_url="https://images.openfoodfacts.org/images/products/12345678/front.1.jpg"),
                   [], None]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(foodstash.ValidationError):
                self.stock.add(data)
        self.assertEqual([], self.stock.list())


class LookupTests(unittest.TestCase):
    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode())

    @patch("foodstash.urlopen")
    def test_found(self, mocked):
        mocked.return_value = self.response({"status": 1, "product": {
            "product_name": "Tomatoes", "brands": "Brand", "quantity": "400 g"}})
        self.assertEqual("Tomatoes", foodstash.lookup_product("0001234567890")["name"])
        request = mocked.call_args.args[0]
        self.assertIn("/0001234567890.json", request.full_url)
        self.assertIn("FoodStash/", request.headers["User-agent"])
        self.assertIn("https://ch.openfoodfacts.org/", request.full_url)
        self.assertIn("cc=ch&lc=de", request.full_url)
        self.assertIn("selected_images", request.full_url)

    @patch("foodstash.urlopen")
    def test_localized_image_preference_and_fallback(self, mocked):
        base = "https://images.openfoodfacts.org/images/products/12345678/"
        for images, expected in [
            ({"de": base + "front_de.1.jpg", "fr": base + "front_fr.1.jpg"}, base + "front_de.1.jpg"),
            ({"fr": base + "front_fr.1.jpg", "it": base + "front_it.1.jpg"}, base + "front_fr.1.jpg"),
            ({"it": base + "front_it.1.jpg"}, base + "front_it.1.jpg"),
            ({}, base + "front.1.jpg"),
            ({"de": "https://evil.example/image.jpg"}, base + "front.1.jpg"),
        ]:
            mocked.return_value = self.response({"status": 1, "product": {
                "code": "12345678", "product_name": "Default", "product_name_de": "Swiss name",
                "selected_images": {"front": {"display": images}},
                "image_front_url": base + "front.1.jpg"}})
            product = foodstash.lookup_product("12345678")
            self.assertEqual(expected, product["image_url"])
            self.assertEqual("Swiss name", product["name"])

    @patch("foodstash.urlopen")
    def test_missing_and_unsafe_images_do_not_break_product(self, mocked):
        for image in [None, "", "http://images.openfoodfacts.org/images/products/12345678/front.jpg",
                      "https://images.openfoodfacts.org.evil.example/images/products/12345678/front.jpg",
                      "https://images.openfoodfacts.org/images/products/../secret.jpg"]:
            mocked.return_value = self.response({"status": 1, "product": {
                "product_name": "Food", "selected_images": None, "image_front_url": image}})
            self.assertEqual("", foodstash.lookup_product("12345678")["image_url"])

    @patch("foodstash.urlopen")
    def test_mismatched_product_is_rejected(self, mocked):
        mocked.return_value = self.response({"status": 1, "product": {
            "code": "87654321", "product_name": "Wrong product"}})
        with self.assertRaises(foodstash.LookupUnavailable):
            foodstash.lookup_product("12345678")

    @patch("foodstash.urlopen")
    def test_not_found_and_unnamed(self, mocked):
        for data in [{"status": 0}, {"status": 1, "product": {}}]:
            mocked.return_value = self.response(data)
            self.assertIsNone(foodstash.lookup_product("12345678"))
        mocked.side_effect = HTTPError("url", 404, "Not found", {}, None)
        self.assertIsNone(foodstash.lookup_product("12345678"))

    @patch("foodstash.urlopen")
    def test_service_errors_are_not_not_found(self, mocked):
        for failure in [URLError("offline"), TimeoutError(), HTTPError("url", 429, "Rate limited", {}, None)]:
            mocked.side_effect = failure
            with self.subTest(failure=failure), self.assertRaises(foodstash.LookupUnavailable):
                foodstash.lookup_product("12345678")

    @patch("foodstash.urlopen")
    def test_bad_upstream_response(self, mocked):
        for response in [b"not json", b"[]" , b"x" * 256_001]:
            mocked.return_value = io.BytesIO(response)
            with self.assertRaises(foodstash.LookupUnavailable):
                foodstash.lookup_product("12345678")

    @patch("foodstash.urlopen")
    def test_invalid_barcode_does_not_make_request(self, mocked):
        with self.assertRaises(foodstash.ValidationError):
            foodstash.lookup_product("../../etc")
        mocked.assert_not_called()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.stock = foodstash.Inventory(Path(self.temp.name) / "data.db")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), foodstash.make_handler(self.stock))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", data=None, headers=None):
        body = None if data is None else json.dumps(data).encode()
        request = Request(self.base + path, data=body, method=method,
                          headers=headers or ({"Content-Type": "application/json"} if body else {}))
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_http_crud_and_health(self):
        self.assertEqual(foodstash.VERSION, self.request("/api/health")[1]["version"])
        status, data = self.request("/api/items", "POST", item())
        self.assertEqual(201, status)
        key = data["item"]["id"]
        self.assertEqual(200, self.request(f"/api/items/{key}", "PATCH", item(name="Edited"))[0])
        self.assertEqual("Edited", self.request("/api/items")[1]["items"][0]["name"])
        self.assertEqual(200, self.request(f"/api/items/{key}/consume", "POST", {})[0])
        self.assertEqual(200, self.request(f"/api/items/{key}", "DELETE", {})[0])
        self.assertEqual(404, self.request(f"/api/items/{key}", "PATCH", item())[0])
        self.assertEqual([], self.request("/api/items")[1]["items"])

    def test_assets_and_unknown_paths(self):
        for path in ["/", "/app.js", "/styles.css"]:
            with urlopen(self.base + path) as response:
                self.assertEqual(200, response.status)
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(404, self.request("/../VERSION")[0])

    def test_validation_and_cross_origin(self):
        self.assertEqual(400, self.request("/api/items", "POST", item(quantity=0))[0])
        self.assertEqual(400, self.request("/api/items", "POST", item(), {
            "Content-Type": "application/json", "Origin": "https://untrusted.example"})[0])
        self.assertEqual(400, self.request("/api/items", "POST", item(), {
            "Content-Type": "text/plain"})[0])
        self.assertEqual([], self.stock.list())

    @patch("foodstash.lookup_product")
    def test_lookup_states_and_manual_fallback(self, lookup):
        lookup.return_value = {"name": "Tomatoes"}
        self.assertEqual("Tomatoes", self.request("/api/products/12345678")[1]["product"]["name"])
        lookup.return_value = None
        self.assertIsNone(self.request("/api/products/12345678")[1]["product"])
        lookup.side_effect = foodstash.LookupUnavailable("Offline. Enter manually.")
        self.assertEqual(502, self.request("/api/products/12345678")[0])
        self.assertEqual(201, self.request("/api/items", "POST", item(barcode=""))[0])
