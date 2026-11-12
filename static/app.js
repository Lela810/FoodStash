"use strict";

const $ = (selector) => document.querySelector(selector);
const dialog = $("#item-dialog");
const form = $("#item-form");
const dateFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });
let items = [];
let filter = "all";
let editingId = null;
let lookupController = null;
let saving = false;
let loadSequence = 0;
let tabletMode = new URL(location.href).searchParams.get("tablet") === "1";
let scanBuffer = "";
let lastScanKey = 0;
let scanSequence = 0;
let scanProduct = null;
let mutationBusy = false;
let autoRefreshing = false;
let inventoryLoaded = false;
const scanDialog = $("#scan-dialog");

function startScannedAdd(barcode, product = null) {
  scanDialog.close();
  openForm();
  $("#barcode").value = barcode;
  if (product) {
    $("#name").value = product.name;
    $("#location").value = product.location;
    setProductPhoto(product.image_url || "", product.name, barcode);
    $("#lookup-status").textContent = "Using saved product details. Choose the expiry date and quantity for a new batch.";
    updateSaveState();
  } else lookup();
}

async function handleScan(barcode) {
  const sequence = ++scanSequence;
  cancelLookup();
  try {
    await loadItems();
    if (sequence !== scanSequence || mutationBusy || saving) return;
    const matches = items.filter((item) => item.barcode === barcode)
      .sort((a, b) => a.expires_on.localeCompare(b.expires_on) || a.id - b.id);
    if (!matches.length) return startScannedAdd(barcode);
    if (dialog.open) dialog.close();
    scanProduct = matches[0];
    $("#scan-title").textContent = scanProduct.name;
    showImage($("#scan-image"), scanProduct.image_url, scanProduct.name);
    $("#scan-photo-source").hidden = !scanProduct.image_url;
    $("#scan-photo-source").href = `https://ch.openfoodfacts.org/product/${encodeURIComponent(barcode)}`;
    $("#scan-summary").textContent = `${matches.reduce((total, item) => total + item.quantity, 0)} in stock across ${matches.length} batches.`;
    $("#scan-batch").textContent = `Remove 1 from the earliest-expiring batch: ${dateFormat.format(new Date(`${scanProduct.expires_on}T12:00:00`))}${scanProduct.location ? ` \u00b7 ${scanProduct.location}` : ""} \u00b7 ${scanProduct.quantity} in this batch.`;
    $("#scan-error").hidden = true;
    $("#scan-add").disabled = false;
    $("#scan-remove").disabled = false;
    if (!scanDialog.open) scanDialog.showModal();
    $("#scan-add").focus();
  } catch (error) {
    notice(`Cannot check current stock: ${error.message} Automatic refresh will retry. Scan again once connected.`, true);
  }
}

$("#scan-add").addEventListener("click", () => {
  if (mutationBusy || !scanProduct) return;
  ++scanSequence;
  startScannedAdd(scanProduct.barcode, scanProduct);
});
$("#scan-remove").addEventListener("click", async () => {
  if (mutationBusy || !scanProduct) return;
  ++scanSequence;
  const batch = scanProduct;
  mutationBusy = true;
  $("#scan-add").disabled = true;
  $("#scan-remove").disabled = true;
  let removed = false;
  try {
    await api(`/api/items/${batch.id}/consume`, "POST", {});
    removed = true;
    scanDialog.close();
    await loadItems();
    notice(`Removed 1 ${batch.name} from the batch expiring ${batch.expires_on}.`);
  } catch (error) {
    if (removed) notice(`One unit removed, but refresh failed: ${error.message} Automatic refresh will retry.`, true);
    else {
      $("#scan-error").textContent = `${error.message} Cancel and scan again to check current stock.`;
      $("#scan-error").hidden = false;
    }
  } finally {
    mutationBusy = false;
  }
});
$("#scan-cancel").addEventListener("click", () => { if (!mutationBusy) scanDialog.close(); });
scanDialog.addEventListener("cancel", (event) => { if (mutationBusy) event.preventDefault(); });

function applyTabletMode() {
  document.body.classList.toggle("tablet-mode", tabletMode);
  $("#tablet-mode").textContent = tabletMode ? "Exit tablet mode" : "Tablet mode";
  $("#tablet-mode").setAttribute("aria-pressed", String(tabletMode));
  $("#name").readOnly = tabletMode;
  $("#quantity").readOnly = tabletMode;
  $("#name").inputMode = tabletMode ? "none" : "text";
  $("#quantity").inputMode = tabletMode ? "none" : "numeric";
  $("#expires_on").inputMode = tabletMode ? "none" : "text";
  scanBuffer = "";
  updateSaveState();
}

function setTabletMode(enabled) {
  tabletMode = enabled;
  const url = new URL(location.href);
  if (tabletMode) url.searchParams.set("tablet", "1");
  else url.searchParams.delete("tablet");
  history.replaceState(null, "", url);
  applyTabletMode();
  $("#desktop-setup").hidden = true;
  $("#retry-lookup").hidden = true;
  render();
}

$("#tablet-mode").addEventListener("click", () => setTabletMode(!tabletMode));
$("#desktop-setup").addEventListener("click", () => {
  setTabletMode(false);
  $("#name").focus();
});
$("#retry-lookup").addEventListener("click", () => lookup());

function updateSaveState() {
  $("#save").disabled = saving || (tabletMode && (!$("#name").value.trim() || !!lookupController));
}

applyTabletMode();

document.addEventListener("keydown", (event) => {
  if (!tabletMode || event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
  const now = performance.now();
  if (now - lastScanKey > 250) scanBuffer = "";
  if (/^[0-9]$/.test(event.key)) {
    event.preventDefault();
    scanBuffer = (scanBuffer + event.key).slice(-32);
    lastScanKey = now;
  } else if (event.key === "Enter" && scanBuffer) {
    event.preventDefault();
    event.stopPropagation();
    const barcode = scanBuffer;
    scanBuffer = "";
    if (saving || mutationBusy) return;
    if (!/^(?:[0-9]{8}|[0-9]{12,14})$/.test(barcode)) {
      notice("Scanner code not supported. Use an 8, 12, 13 or 14 digit food barcode with an Enter suffix.", true);
      return;
    }
    handleScan(barcode);
  } else if (event.key === "Enter") {
    // Ignore duplicate/late scanner suffixes rather than activating a focused button.
    event.preventDefault();
  } else if (event.key.length === 1) {
    scanBuffer = "";
  }
}, true);

function localDate(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function daysUntil(value) {
  const today = new Date();
  return Math.round((new Date(`${value}T00:00:00Z`).getTime() -
    Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
}

async function api(path, method = "GET", data) {
  const response = await fetch(path, {
    method,
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The request could not be completed.");
  return payload;
}

function notice(message, error = false) {
  $("#notice").textContent = message;
  $("#notice").classList.toggle("error", error);
  $("#notice").hidden = !message;
}

function showImage(container, url, name, onError = () => {}) {
  container.textContent = name.charAt(0).toUpperCase();
  if (!url) return;
  const image = document.createElement("img");
  image.alt = name;
  image.referrerPolicy = "no-referrer";
  image.loading = "lazy";
  image.addEventListener("error", () => {
    if (container.firstChild !== image) return;
    container.textContent = name.charAt(0).toUpperCase();
    onError();
  }, { once: true });
  image.src = url;
  container.replaceChildren(image);
}

function setProductPhoto(url, name = "", barcode = "") {
  $("#image_url").value = url;
  $("#product-photo").hidden = !url;
  $("#lookup-image").replaceChildren();
  if (!url) return;
  $("#photo-source").href = `https://ch.openfoodfacts.org/product/${encodeURIComponent(barcode)}`;
  showImage($("#lookup-image"), url, name, () => {
    $("#lookup-status").textContent += " Photo could not load; you can still save the product.";
  });
}

function render() {
  const soon = items.filter((item) => daysUntil(item.expires_on) >= 0 && daysUntil(item.expires_on) <= 7);
  $("#stat-total").textContent = items.reduce((total, item) => total + item.quantity, 0);
  $("#stat-batches").textContent = items.length;
  $("#stat-soon").textContent = soon.reduce((total, item) => total + item.quantity, 0);
  $("#stat-expired").textContent = items.filter((item) => daysUntil(item.expires_on) < 0)
    .reduce((total, item) => total + item.quantity, 0);
  const query = tabletMode ? "" : $("#search").value.trim().toLowerCase();
  const visible = items.filter((item) => {
    const days = daysUntil(item.expires_on);
    return (filter === "all" || (filter === "soon" && days >= 0 && days <= 7) ||
      (filter === "expired" && days < 0)) &&
      `${item.name} ${item.barcode} ${item.location} ${item.notes}`.toLowerCase().includes(query);
  });
  $("#inventory").replaceChildren();
  for (const item of visible) {
    const row = $("#item-template").content.cloneNode(true);
    const days = daysUntil(item.expires_on);
    row.querySelector("h3").textContent = item.name;
    showImage(row.querySelector(".product-icon"), item.image_url, item.name);
    if (item.image_url) {
      const source = document.createElement("a");
      source.href = `https://ch.openfoodfacts.org/product/${encodeURIComponent(item.barcode)}`;
      source.target = "_blank";
      source.rel = "noopener noreferrer";
      source.className = "image-credit";
      source.textContent = "Photo: Open Food Facts (CC BY-SA)";
      row.querySelector(".product-info").append(source);
    }
    row.querySelector(".product-meta").textContent = [item.location || "No shelf assigned", item.barcode].filter(Boolean).join(" \u00b7 ");
    row.querySelector(".product-notes").textContent = item.notes;
    const badge = row.querySelector(".badge");
    badge.textContent = days < 0 ? `${Math.abs(days)}d past expiry` : days === 0 ? "Expires today" : days <= 7 ? `${days}d left \u00b7 use soon` : `${days} days left`;
    badge.classList.toggle("expired", days < 0);
    badge.classList.toggle("soon", days >= 0 && days <= 7);
    row.querySelector(".expiry small").textContent = dateFormat.format(new Date(`${item.expires_on}T12:00:00`));
    row.querySelector(".stock strong").textContent = item.quantity;
    const consume = row.querySelector(".consume");
    consume.setAttribute("aria-label", `Use one ${item.name}`);
    consume.addEventListener("click", () => changeItem(consume, item, "consume"));
    row.querySelector(".edit").addEventListener("click", () => openForm(item));
    const remove = row.querySelector(".delete");
    remove.setAttribute("aria-label", `Delete ${item.name} batch`);
    remove.addEventListener("click", () => {
      if (confirm(`Remove this entire batch of ${item.quantity} \u00d7 ${item.name}?`)) changeItem(remove, item, "delete");
    });
    $("#inventory").append(row);
  }
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    const title = document.createElement("h3");
    title.textContent = items.length ? "No reserves match this view." : "Your shelves are ready.";
    const text = document.createElement("p");
    text.textContent = items.length ? "Try another search or expiry filter." : "Scan your first product, add its expiry date, and give everything in your cellar a place.";
    empty.append(title, text);
    if (!items.length) {
      const add = document.createElement("button");
      add.className = "primary";
      add.textContent = "+ Add your first product";
      add.addEventListener("click", () => openForm());
      empty.append(add);
    }
    $("#inventory").append(empty);
  }
  $("#result-count").textContent = `${visible.length} of ${items.length} batches`;
}

async function loadItems() {
  const sequence = ++loadSequence;
  const data = await api("/api/items");
  if (sequence !== loadSequence) return;
  const changed = !inventoryLoaded || JSON.stringify(items) !== JSON.stringify(data.items);
  items = data.items;
  inventoryLoaded = true;
  $("#sync-status").textContent = "Auto-sync connected";
  $("#sync-status").classList.remove("sync-error");
  if (changed) render();
}

async function autoRefresh() {
  if (document.hidden || autoRefreshing || mutationBusy || saving) return;
  autoRefreshing = true;
  try {
    await loadItems();
  } catch (error) {
    $("#sync-status").textContent = `Stock may be out of date: ${error.message} Retrying automatically.`;
    $("#sync-status").classList.add("sync-error");
    if (!inventoryLoaded) {
      $("#inventory").textContent = "Inventory is unavailable. Checking connection automatically...";
    }
  } finally {
    autoRefreshing = false;
  }
}

function cancelLookup() {
  if (lookupController) lookupController.abort();
  lookupController = null;
  $("#lookup").disabled = false;
}

function openForm(item = null) {
  cancelLookup();
  form.reset();
  setProductPhoto(item ? item.image_url : "", item ? item.name : "", item ? item.barcode : "");
  editingId = item ? item.id : null;
  $("#dialog-title").textContent = item ? "Edit this batch" : "Add to your reserves";
  $("#save").textContent = item ? "Save changes" : "Save to reserves";
  $("#form-error").hidden = true;
  $("#desktop-setup").hidden = true;
  $("#retry-lookup").hidden = true;
  $("#lookup-status").textContent = tabletMode
    ? "Scan a product now. No need to tap a barcode field. Product details fill automatically."
    : "Use a USB / Bluetooth scanner, or enter a product manually below.";
  $("#lookup-status").classList.remove("lookup-error");
  if (item) {
    for (const key of ["name", "barcode", "expires_on", "quantity", "location", "notes"]) {
      form.elements.namedItem(key).value = item[key];
    }
  }
  updateSaveState();
  if (!dialog.open) dialog.showModal();
  $("#nav-add").classList.add("active");
  $("#nav-inventory").classList.remove("active");
  $("#nav-inventory").removeAttribute("aria-current");
  (tabletMode ? $("#close-dialog") : item ? $("#name") : $("#barcode")).focus();
}

function closeForm() {
  if (saving) return;
  cancelLookup();
  dialog.close();
}

async function lookup() {
  cancelLookup();
  setProductPhoto("");
  const barcode = $("#barcode").value.trim();
  const known = tabletMode ? items.find((item) => item.barcode === barcode) : null;
  const status = $("#lookup-status");
  $("#desktop-setup").hidden = true;
  $("#retry-lookup").hidden = true;
  status.classList.remove("lookup-error");
  if (!/^(?:[0-9]{8}|[0-9]{12,14})$/.test(barcode)) {
    status.textContent = "Look up an 8, 12, 13 or 14 digit food barcode, or enter the product manually.";
    status.classList.add("lookup-error");
    return;
  }
  const controller = new AbortController();
  lookupController = controller;
  updateSaveState();
  $("#lookup").disabled = true;
  status.textContent = "Searching Open Food Facts Switzerland...";
  try {
    const response = await fetch(`/api/products/${barcode}`, { signal: controller.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Product lookup failed.");
    if (controller.signal.aborted || $("#barcode").value.trim() !== barcode) return;
    const product = data.product || known;
    if (product) {
      $("#name").value = product.name;
      if (known) $("#location").value = known.location;
      status.textContent = data.product
        ? `Found on Open Food Facts Switzerland${product.brand ? ` \u00b7 ${product.brand}` : ""}${product.size ? ` \u00b7 ${product.size}` : ""}. Check the name against your label.${product.image_url ? "" : " No product photo available."}`
        : "Using this barcode's saved product details. Choose the expiry date and quantity.";
      setProductPhoto(product.image_url || "", product.name, barcode);
      if (!tabletMode) $("#expires_on").focus();
    } else {
      status.textContent = tabletMode
        ? "Unknown product. Scan another product, or switch to desktop mode to set up its name. Nothing has been saved."
        : "No named product found. Enter the product name below; you can still save it.";
      $("#desktop-setup").hidden = !tabletMode;
      if (!tabletMode) $("#name").focus();
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      if (tabletMode && known) {
        $("#name").value = known.name;
        $("#location").value = known.location;
        setProductPhoto(known.image_url || "", known.name, barcode);
        status.textContent = `${error.message} Using saved details for this barcode. Choose the expiry date and quantity.`;
      } else {
        status.textContent = tabletMode
          ? `${error.message} Retry, scan another product, or switch to desktop mode for manual setup. Nothing has been saved.`
          : `${error.message} Manual entry is always available.`;
        $("#desktop-setup").hidden = !tabletMode;
      }
      $("#retry-lookup").hidden = !tabletMode;
      status.classList.add("lookup-error");
    }
  } finally {
    if (lookupController === controller) {
      lookupController = null;
      $("#lookup").disabled = false;
      updateSaveState();
    }
  }
}

async function changeItem(button, item, action) {
  if (mutationBusy) return;
  mutationBusy = true;
  button.disabled = true;
  let changed = false;
  try {
    await api(`/api/items/${item.id}${action === "consume" ? "/consume" : ""}`,
      action === "consume" ? "POST" : "DELETE", {});
    changed = true;
    await loadItems();
    notice(action === "consume" ? `One ${item.name} used. Inventory updated.` : "Batch removed.");
  } catch (error) {
    notice(changed ? `Change saved, but refreshing failed: ${error.message} Automatic refresh will retry.` : error.message, true);
  } finally {
    mutationBusy = false;
    button.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saving || (tabletMode && !$("#name").value.trim())) return;
  cancelLookup();
  saving = true;
  $("#save").disabled = true;
  $("#form-error").hidden = true;
  const data = Object.fromEntries(new FormData(form));
  data.quantity = Number(data.quantity);
  let saved = false;
  try {
    await api(editingId ? `/api/items/${editingId}` : "/api/items", editingId ? "PATCH" : "POST", data);
    saved = true;
    dialog.close();
    await loadItems();
    notice(editingId ? "Batch updated. Everything in its place." : "Added to your reserves. Ready for the next scan.");
  } catch (error) {
    if (saved) notice(`Saved, but refreshing failed: ${error.message} Automatic refresh will retry; do not add it again.`, true);
    else {
      $("#form-error").textContent = error.message;
      $("#form-error").hidden = false;
    }
  } finally {
    saving = false;
    updateSaveState();
  }
});

$("#barcode").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); lookup(); }
});
$("#barcode").addEventListener("input", () => {
  cancelLookup();
  setProductPhoto("");
  $("#lookup-status").textContent = "Barcode changed. Look up this product or enter its name manually.";
});
$("#lookup").addEventListener("click", lookup);
$("#open-add").addEventListener("click", () => openForm());
$("#nav-add").addEventListener("click", () => openForm());
$("#nav-inventory").addEventListener("click", () => { if (!dialog.open) $("#search").focus(); });
$("#close-dialog").addEventListener("click", closeForm);
$("#cancel").addEventListener("click", closeForm);
dialog.addEventListener("cancel", (event) => { if (saving) event.preventDefault(); });
dialog.addEventListener("close", () => {
  cancelLookup();
  $("#nav-add").classList.remove("active");
  $("#nav-inventory").classList.add("active");
  $("#nav-inventory").setAttribute("aria-current", "page");
});
$("#quantity-minus").addEventListener("click", () => {
  $("#quantity").value = Math.max(1, (Number($("#quantity").value) || 1) - 1);
});
$("#quantity-plus").addEventListener("click", () => {
  $("#quantity").value = Math.min(9999, (Number($("#quantity").value) || 0) + 1);
});
document.querySelectorAll("[data-days]").forEach((button) => button.addEventListener("click", () => {
  const date = new Date();
  date.setDate(date.getDate() + Number(button.dataset.days));
  $("#expires_on").value = localDate(date);
}));
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((entry) => {
    entry.classList.toggle("active", entry === button);
    entry.setAttribute("aria-pressed", String(entry === button));
  });
  render();
}));
$("#search").addEventListener("input", render);
api("/api/health").then((data) => { $("#version").textContent = `v${data.version}`; })
  .catch((error) => notice(`Server connection failed: ${error.message}`, true));
autoRefresh();
setInterval(autoRefresh, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) autoRefresh(); });
window.addEventListener("focus", autoRefresh);
// Recalculate expiry badges after midnight without requiring a page reload.
setInterval(() => { if (items.length) render(); }, 60000);
