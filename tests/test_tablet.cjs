// Small DOM harness exercises the real scanner/mode code without installing a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.handlers = {};
    this.classes = new Set();
    this.classList = {
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
      add: (name) => this.classes.add(name),
      remove: (name) => this.classes.delete(name),
    };
  }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  setAttribute() {}
  removeAttribute() {}
  append() {}
  querySelector() { return new Element(); }
  cloneNode() { return new Element(); }
  replaceChildren() {}
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.handlers.close?.(); }
}

async function main() {
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const handlers = {};
  const location = { href: "http://localhost/?tablet=1" };
  let product = { name: "Scanner test food", image_url: "", brand: "", size: "" };
  let lookups = 0;
  let stock = [];
  let failInventory = false;
  let mutations = 0;
  const intervals = [];
  const windowHandlers = {};
  const document = {
    body: new Element(),
    querySelector: element,
    querySelectorAll: () => [],
    createElement: () => new Element(),
    addEventListener: (name, callback) => { handlers[name] = callback; },
  };
  element("#item-form").reset = () => {
    for (const id of ["name", "barcode", "image_url", "expires_on", "location", "notes"]) element("#" + id).value = "";
    element("#quantity").value = "1";
  };
  element("#item-form").elements = { namedItem: (name) => element("#" + name) };
  element("#item-template").content = new Element();
  const context = vm.createContext({
    document, location, URL, Intl, Date, AbortController, console,
    history: { replaceState: (_state, _title, url) => { location.href = url.href; } },
    performance: { now: () => Date.now() },
    window: { addEventListener: (name, callback) => { windowHandlers[name] = callback; } },
    setInterval: (callback, ms) => intervals.push({callback, ms}),
    fetch: async (url, options = {}) => {
      if (url.startsWith("/api/products/")) lookups++;
      if (url.endsWith("/consume")) {
        mutations++;
        const id = Number(url.split("/")[3]);
        const batch = stock.find(item => item.id === id);
        if (batch.quantity === 1) stock = stock.filter(item => item.id !== id);
        else batch.quantity--;
      }
      if (url === "/api/items" && failInventory) throw new Error("Offline test");
      return { ok: true, json: async () => url === "/api/items" ? { items: JSON.parse(JSON.stringify(stock)) }
        : url === "/api/health" ? { version: "test" } : { product } };
    },
  });
  vm.runInContext(fs.readFileSync("static/app.js", "utf8"), context);
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const scan = (barcode) => {
    for (const key of [...barcode, "Enter"]) {
      const event = { key, preventDefault() { this.prevented = true; }, stopPropagation() {} };
      handlers.keydown(event);
      assert.equal(event.prevented, true, `Scanner ${key} must not reach a focused control`);
    }
  };
  await settle();
  assert.equal(element("#name").readOnly, true);
  assert.equal(element("#quantity").readOnly, true);
  assert.equal(element("#save").disabled, true);
  scan("3017620422003");
  await settle();
  assert.equal(element("#item-dialog").open, true);
  assert.equal(element("#name").value, product.name);
  assert.equal(element("#save").disabled, false);
  assert.equal(lookups, 1);
  const suffix = { key: "Enter", preventDefault() { this.prevented = true; } };
  handlers.keydown(suffix);
  assert.equal(suffix.prevented, true, "Duplicate scanner suffix must not activate a focused button");
  product = null;
  scan("0000000000000");
  await settle();
  assert.equal(element("#name").value, "");
  assert.equal(element("#save").disabled, true);
  assert.equal(element("#desktop-setup").hidden, false);
  assert.match(element("#lookup-status").textContent, /Unknown product/);
  element("#desktop-setup").handlers.click();
  assert.equal(element("#name").readOnly, false);
  assert.equal(new URL(location.href).searchParams.has("tablet"), false);
  const before = lookups;
  handlers.keydown({ key: "3" });
  handlers.keydown({ key: "Enter" });
  assert.equal(lookups, before, "Desktop mode must not intercept keys");
  element("#tablet-mode").handlers.click();
  assert.equal(new URL(location.href).searchParams.get("tablet"), "1");
  assert.equal(element("#name").readOnly, true);
  stock = [
    {id: 2, barcode:"3017620422003", name:"Stored food", image_url:"", expires_on:"2028-03-01", quantity:3, location:"Shelf B", notes:""},
    {id: 1, barcode:"3017620422003", name:"Stored food", image_url:"", expires_on:"2027-01-01", quantity:2, location:"Shelf A", notes:""},
  ];
  scan("3017620422003");
  await settle();
  assert.equal(element("#scan-dialog").open, true);
  assert.match(element("#scan-summary").textContent, /5 in stock across 2 batches/);
  assert.match(element("#scan-batch").textContent, /Shelf A/);
  assert.equal(mutations, 0, "Scan alone must never mutate inventory");
  element("#scan-add").handlers.click();
  assert.equal(element("#scan-dialog").open, false);
  assert.equal(element("#name").value, "Stored food");
  assert.equal(element("#expires_on").value, "", "New batch must require its own expiry date");
  scan("3017620422003");
  await settle();
  const removal = element("#scan-remove").handlers.click();
  element("#scan-remove").handlers.click();
  await removal;
  assert.equal(mutations, 1, "Duplicate remove taps must not remove twice");
  assert.equal(stock.find(item=>item.id===1).quantity, 1);
  scan("3017620422003");
  await settle();
  await element("#scan-remove").handlers.click();
  assert.equal(stock.length, 1, "Last unit removes just that batch");
  assert.equal(stock[0].id, 2);
  assert.equal(stock[0].quantity, 3);
  const poll = intervals.find(timer=>timer.ms===5000);
  assert.ok(poll, "Must poll inventory");
  document.hidden = true;
  stock = [];
  await poll.callback();
  assert.equal(element("#stat-total").textContent, 3, "Hidden tab must pause polling");
  document.hidden = false;
  handlers.visibilitychange();
  await settle();
  assert.equal(element("#stat-total").textContent, 0);
  failInventory = true;
  await poll.callback();
  assert.match(element("#sync-status").textContent, /out of date.*Offline test/);
  failInventory = false;
  windowHandlers.focus();
  await settle();
  assert.equal(element("#sync-status").textContent, "Auto-sync connected");
  assert.equal(elements.has("#refresh"), false, "Refresh button is no longer wired");
  console.log("PASS: tablet scanner, unknown guard, in-stock Add/Remove, earliest batch/quantities, duplicate mutation guard, visible polling, visibility/focus refresh and failure recovery.");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
