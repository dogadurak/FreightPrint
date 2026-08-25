/**
 * Does a hostile place name in an uploaded file reach the page as markup?
 *
 * Lane keys, carrier names and leg endpoints all come out of a shipment file, which is
 * a stranger's text even when the stranger is a colleague. The engine treats them as
 * labels the whole way through and is right to — the escaping belongs at the one point
 * the string becomes markup, and this checks it is actually there.
 *
 *     node scripts/check_escaping.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const script = fs.readFileSync(path.join(root, "frontend/app.js"), "utf8");

const PAYLOAD = '<img src=x onerror=alert(1)>';

// Only `escapeHtml` is needed, so the file is evaluated for its declarations alone and
// its top-level wiring is left unrun.
const context = { console };
vm.createContext(context);
const declarations = script.match(/function escapeHtml\(value\) \{[\s\S]*?\n\}/);
if (!declarations) {
  console.error("escapeHtml is gone; externally-sourced text has nothing escaping it");
  process.exit(1);
}
vm.runInContext(declarations[0] + "\nthis.escapeHtml = escapeHtml;", context);

// What matters is that no angle bracket survives: `onerror=` sitting in the output as
// text is inert once `<` cannot open a tag, and asserting on the word rather than the
// bracket would fail a correct implementation.
const escaped = context.escapeHtml(PAYLOAD);
if (/[<>]/.test(escaped) || !escaped.includes("&lt;img")) {
  console.error(`escapeHtml let markup through: ${escaped}`);
  process.exit(1);
}
for (const [raw, entity] of [['"', "&quot;"], ["'", "&#39;"], ["&", "&amp;"]]) {
  if (context.escapeHtml(raw) !== entity) {
    console.error(`escapeHtml(${raw}) gave ${context.escapeHtml(raw)}, expected ${entity}`);
    process.exit(1);
  }
}

// Every interpolation of a name that came from a file has to go through it. Checked as
// text because the alternative is rendering the whole dashboard, and the rule is simple
// enough to state directly.
const mustEscape = [
  "${escapeHtml(lane.key)}",
  "${escapeHtml(c.carrier)}",
  "${escapeHtml(leg.from_name)}",
  "${escapeHtml(leg.to_name)}",
];
const missing = mustEscape.filter((needle) => !script.includes(needle));
if (missing.length) {
  console.error(`interpolated raw into markup: ${missing.join(", ")}`);
  process.exit(1);
}

console.log(`temiz (escapeHtml calisiyor, ${mustEscape.length} dis kaynakli alan kacisli)`);
