# Changelog

## 0.1.0 - 2026-11-12

- Initial cellar inventory app with keyboard-wedge barcode scanning and Open Food Facts lookup.
- Swiss-context exact-barcode lookup with German/French/Italian front-photo preference, persisted photos and graceful image fallbacks.
- Keyboard-free tablet layout via the Tablet mode button or `?tablet=1`, with global scanner capture, automatic product details, touch dates/quantity and hidden typing-only fields.
- In-stock tablet scans offer Add or Remove 1 with earliest-expiry batch details; out-of-stock scans open Add directly.
- Visibility-aware automatic inventory synchronization replaces the Refresh button and reports connection failures.
- Touchscreen-friendly expiry dates, separate stock batches, search, expiry filters, editing and consumption.
- Persistent SQLite inventory, non-root Docker image and local-first Compose deployment.
- Version-consistent documentation and stable-tag-only multi-platform GHCR publishing.
