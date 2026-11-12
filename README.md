# FoodStash

Current release: **v0.1.0**

Your cellar, in order. A small, self-hosted web app for scanning food reserves, recording expiry dates on a touchscreen, and knowing what to use next.

## What it does

- USB/Bluetooth **keyboard-wedge scanners**: scan a barcode, look up the product on Open Food Facts, check its name, enter the printed expiry date and save.
- Large touch controls, native date picker, date shortcuts, quantity buttons and responsive tablet/phone layouts.
- Separate batches even for the same barcode, with quantities, shelf/location and notes.
- Expiry-first inventory, search, use-soon (today through seven days), past-expiry indicators, batch editing, consume one unit and confirmed batch removal.
- SQLite persistence without external services or runtime packages. Stock management and manual entry work without internet once the page is loaded.

Expiry indicators are reminders, not food-safety advice. Always check the product label; the app does not distinguish best-before from use-by dates.

## Run locally with Docker

Install Docker Engine/Desktop with Linux containers and the Compose plugin. From this checkout:

```sh
docker compose up -d --build
docker compose logs -f foodstash
```

Open **http://localhost:8080**. The first run has an empty inventory (no demo stock). To stop:

```sh
docker compose down
```

The local build is allowed at any time; only **GitHub automated builds/publication** are release-only. Compose uses a named `inventory` volume mounted at `/data`; container recreation and `docker compose down` retain it. **Do not use `docker compose down -v` unless you intend to delete your inventory.** Keep using the same Compose project name/directory, or explicitly set `COMPOSE_PROJECT_NAME=foodstash` from the start, so subsequent launches reuse the same volume.

### Use a published release

After the first release tag has been pushed and its workflow succeeds, the exact image is:

```text
ghcr.io/lela810/foodstash:0.1.0
```

No image is published merely by committing this source. From this checkout, use the registry image instead of building:

```sh
docker compose pull
docker compose up -d --no-build
```

Only exact version tags are published, not `latest` or moving branch tags. To upgrade, use the matching release checkout, or set `FOODSTASH_VERSION` to the desired released version, pull, then recreate the container. Back up before upgrading. GHCR packages may initially be private: the owner can make the package public in GitHub package settings, or users can `docker login ghcr.io` with a token permitted to read the package.

### Cellar touchscreen / LAN access

The default published port binds to **127.0.0.1 only**. For a separate cellar tablet on a trusted network, set `FOODSTASH_BIND` to the Docker host's LAN IP (or `0.0.0.0` to bind every interface), then run `docker compose up -d`. Open `http://HOST-LAN-IP:8080` on the tablet and allow that port only from your trusted LAN in the host firewall. Example `.env` beside `compose.yaml`:

```dotenv
FOODSTASH_BIND=192.168.1.50
```

**There is no login, user separation or TLS. Everyone who can reach the app can view and change inventory. Do not expose port 8080 to the public internet or an untrusted Wi-Fi network.** Use a separately configured authenticated HTTPS reverse proxy or VPN if you need remote access; neither is bundled. One app instance owns one inventory database. Multiple browser clients are supported. Inventory refreshes automatically every five seconds while visible, immediately after changes, and when returning to the tab/window. Hidden tabs pause polling. Connection failures show a stale-stock warning and retry automatically; no Refresh button is needed.

## Scanner and lookup

### Touch-only tablet mode

Tap **Tablet mode** at the top of the page, or open/bookmark **http://localhost:8080/?tablet=1** (use the host's LAN IP instead of localhost on a separate tablet). **Exit tablet mode** restores the regular layout and removes the parameter. Reloading/bookmarking the tablet URL keeps it enabled; it is not silently enabled for other devices.

Tablet mode requires **no physical or on-screen keyboard**. While the browser page is active, scan anywhere using a keyboard-wedge scanner configured with an **Enter/CR suffix** and less than 250 ms between characters. There is no barcode field to focus. The app checks current stock: if the barcode is in storage, a popup shows its product/photo, total quantity and earliest-expiring batch, with **Add stock** or **Remove 1**. Add prefills a new batch; Remove consumes exactly one unit from the displayed earliest-expiring batch (oldest batch ID breaks expiry ties), deleting that batch only when its last unit is used. It never removes an arbitrary batch or the whole product. If another client already removed that batch, cancel and rescan to see current stock.

An out-of-stock barcode opens Add immediately and looks up its details. A new scan never overwrites an existing batch being edited, and scanning alone never changes inventory. Choose the date with the native touch date picker or quick date buttons and quantity with the large -/+ buttons; then tap Save. Edit, Use 1 and confirmed whole-batch removal also work by touch. Scanner digits and duplicate Enter suffixes are intercepted before they can change the date or trigger a focused button.

Free-text search, notes and shelf-entry fields are hidden in tablet mode; product name and quantity are read-only (quantity changes through buttons). Existing notes/shelf values are preserved on edits, and a known barcode can reuse saved name/photo/shelf details if lookup fails or has no result. Unknown products cannot be saved with invented details: tap Retry lookup, scan another product, or tap **Set up product in desktop mode** for manual setup. All free-text/manual fields remain available in desktop mode. No camera or custom keyboard is included.

1. Pair the scanner with the touchscreen computer/tablet in HID/keyboard mode. No scanner needs attaching to the Docker host.
2. Configure an **Enter/CR suffix**, tap **Add to reserves**, and scan with the barcode field focused. Enter starts the lookup automatically. Without a suffix, tap **Look up**.
3. Check the returned product name, select the printed date with the touch date picker, adjust quantity and save. Tap Add again for the next product. Each save creates its own batch.

EAN-8, UPC-A, EAN-13 and GTIN-14 lengths are accepted for lookup. Leading zeroes are preserved. Other numeric barcodes (4-32 digits) can be saved manually. No camera scanning or proprietary scanner integration is included.

Lookup sends only the scanned barcode from the server to [Open Food Facts Switzerland](https://ch.openfoodfacts.org/), requires outbound HTTPS/DNS, and times out after eight seconds. It requests the exact barcode with Swiss country context, prefers German, French, then Italian names/front photos, and falls back to the same product's default name/photo. Switzerland context is not a guarantee of Swiss availability or Swiss packaging; an unrelated product is never substituted. Photos appear during lookup and on saved batches. Existing batches can get a photo by opening Edit and looking up the barcode again.

Products/photos may be missing or incorrect: not-found and service/network errors are shown distinctly, missing/broken images fall back to a product initial, and **manual product entry remains available**. Changing the barcode clears the previous photo. Photo URLs are stored with each batch (existing inventories are migrated automatically without losing stock); images are loaded directly by your browser from `images.openfoodfacts.org`, so images require internet and that service receives the browser's image request/IP. Images are not downloaded into the inventory volume. Photo/source links credit Open Food Facts contributors under [CC BY-SA](https://world.openfoodfacts.org/terms-of-use); database data is community-maintained under the Open Database License.

Expiry dates are not encoded in ordinary retail barcodes; enter the date from the package. Inventory, locations and notes are never sent to the product service. Verify product names and photos against labels.

## Persistence, backup and restore

The database is `/data/foodstash.db`, including SQLite WAL files while running. Do not copy just the main file from a running container. The following commands use SQLite's online backup API to create a consistent snapshot and then copy it to the current directory:

```sh
docker compose exec foodstash python -c "import sqlite3; source=sqlite3.connect('/data/foodstash.db'); target=sqlite3.connect('/data/foodstash-backup.db'); source.backup(target); target.execute('PRAGMA journal_mode=DELETE'); target.close(); source.close()"
docker compose cp foodstash:/data/foodstash-backup.db ./foodstash-backup.db
docker compose exec foodstash python -c "from pathlib import Path; Path('/data/foodstash-backup.db').unlink()"
```

Store backups somewhere separate from the Docker host. To restore, stop the app, remove the old database and its sidecars from the volume using a one-off service container, and copy the verified backup in:

```sh
docker compose stop foodstash
docker compose run --rm --no-deps --entrypoint python foodstash -c "from pathlib import Path; [p.unlink(missing_ok=True) for p in (Path('/data/foodstash.db'), Path('/data/foodstash.db-wal'), Path('/data/foodstash.db-shm'))]"
docker compose run --rm --no-deps --entrypoint python -v ./foodstash-backup.db:/backup.db:ro foodstash -c "import sqlite3; source=sqlite3.connect('file:/backup.db?mode=ro', uri=True); target=sqlite3.connect('/data/foodstash.db'); source.backup(target); target.close(); source.close()"
docker compose up -d --no-build
```

Verify the backup opens successfully **before removing the existing database** (for example `python -c "import sqlite3; print(sqlite3.connect('file:foodstash-backup.db?mode=ro',uri=True).execute('PRAGMA integrity_check').fetchone())"` should print `('ok',)`). Restore overwrites inventory; keep an additional backup of the current data first. On Windows PowerShell, use the same commands with Docker Desktop; use an absolute Windows path for the backup bind mount if a relative path is not accepted.

## Development and checks

Python 3.13+ and a modern browser are sufficient; Node.js is needed only for JS syntax checking. No `pip install` or `npm install` is required.

```sh
python foodstash.py
python -m unittest discover -s tests -v
python scripts/check_release.py
node --check static/app.js
node tests/test_tablet.cjs
docker compose config --quiet
```

Direct Python startup listens on all interfaces at port 8080 by default; set `FOODSTASH_HOST=127.0.0.1` for local-only development. `FOODSTASH_DB` changes the database path (default `data/foodstash.db` locally); `PORT` changes the server port. The Docker configuration uses `/data/foodstash.db` and 8080. For Docker integration validation: `python scripts/test_docker.py` builds a uniquely named local test image, checks real container startup, CRUD/manual entry, persistence after recreation and backup/restore, and cleans up its own containers/volume/image. It does not publish images or affect your Compose inventory.

## Making a release

Ordinary branch pushes and pull requests run checks only: **they do not build or publish a container**. The release workflow is triggered only by a pushed `vX.Y.Z` tag, and rejects prerelease tags, leading zeroes, malformed tags and version/documentation mismatches before reaching the build job.

1. Choose a new stable semantic version. Update `VERSION`, **this README's current release and image example**, `compose.yaml` default and the first release heading/notes in `CHANGELOG.md` in one commit. The app reads `VERSION` directly.
2. Run the checks above, including local Docker validation, and test the app locally. Commit the changes.
3. Obtain approval before pushing. Push the branch, then create and push the matching tag, for example `git tag v0.1.0` and `git push origin v0.1.0`.
4. GitHub Actions validates the tag against release metadata, tests, and refuses an already-published version; then builds `linux/amd64` and `linux/arm64` images and publishes `ghcr.io/lela810/foodstash:X.Y.Z` using `GITHUB_TOKEN` (`packages: write`). Enable Actions for the repository and permit package publishing. No registry password secret is needed.

Release tags and image versions are immutable: never move/reuse a tag. A workflow rerun after an image exists deliberately fails rather than overwriting it; fix code with a new version/tag. README/changelog updates are intentionally reviewed source changes, not bot commits after publication, so the tagged source, running app and docs agree. No deployment happens automatically.
