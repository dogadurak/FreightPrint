/**
 * Does app.js get all the way through its own initialisation against index.html?
 *
 * A syntax error or a `null.addEventListener` at module scope kills the whole script,
 * and the browser says nothing anyone will see: every form quietly falls back to a
 * native submission. That is exactly how a stray `});` left the dashboard sending
 * `GET /?origin=...&tonnage=24` instead of `POST /api/routes` — the page looked fine,
 * the button appeared to do nothing, and no Python test could notice.
 *
 * So this runs the real file against a stubbed DOM built from the real markup:
 * `getElementById` answers only for ids that genuinely appear in index.html, and
 * returns null for anything else, which is what the browser would do.
 *
 *     node scripts/check_frontend_boots.js
 *
 * Exits non-zero on the first thing that would have thrown in a browser.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const page = fs.readFileSync(path.join(root, "frontend/index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "frontend/app.js"), "utf8");

const ids = new Set([...page.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

/** A stand-in element that answers anything without pretending to be a browser. */
function element(id = "") {
  const node = {
    id,
    tagName: "DIV",
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    checked: false,
    dataset: {},
    style: {},
    children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    elements: new Proxy({}, { get: () => element() }),
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    getAttribute: () => null,
    append() {},
    appendChild() {},
    replaceChildren() {},
    remove() {},
    closest: () => element(),
    querySelector: () => element(),
    querySelectorAll: () => [],
    focus() {},
    blur() {},
    scrollIntoView() {},
    getBoundingClientRect: () => ({ width: 800, height: 400, top: 0, left: 0 }),
    insertAdjacentHTML() {},
    click() {},
  };
  return node;
}

const document = {
  // The whole point: an id the markup does not carry comes back null, as it would.
  getElementById: (id) => (ids.has(id) ? element(id) : null),
  createElement: () => element(),
  createTextNode: (text) => ({ textContent: text }),
  querySelector: () => element(),
  querySelectorAll: () => [],
  addEventListener() {},
  documentElement: element(),
  body: element(),
  head: element(),
};

const sandbox = {
  document,
  console,
  setTimeout: () => 0,
  clearTimeout() {},
  setInterval: () => 0,
  clearInterval() {},
  requestAnimationFrame: () => 0,
  cancelAnimationFrame() {},
  // Never resolves: initialisation must not depend on a server answering.
  fetch: () => new Promise(() => {}),
  URL: { createObjectURL: () => "blob:", revokeObjectURL() {} },
  Intl,
  Math,
  JSON,
  Date,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  // Deliberately absent, so the "map failed to load" path is the one exercised. The
  // dashboard is required to survive it — losing the map must not lose the numbers.
  maplibregl: undefined,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

try {
  vm.createContext(sandbox);
  new vm.Script(script, { filename: "frontend/app.js" }).runInContext(sandbox);
} catch (error) {
  console.error("frontend/app.js tarayicida yuklenirken patlar:\n");
  console.error(error.stack ? error.stack.split("\n").slice(0, 6).join("\n") : error);
  process.exit(1);
}

console.log(`temiz (app.js ${ids.size} id tasiyan sayfaya karsi sorunsuz yuklendi)`);
