// Attested content digest, algorithm ACD-1: an implementation independent of acd.py, in JavaScript (Node 18+).
// It shares no code with the Python reference; the profile build requires both to agree on every vector.
//   node acd.mjs FILE.json [--canonical]
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

class ACDError extends Error {}
const NUM = Symbol("number");

function parse(bytes) {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) throw new ACDError("byte order mark");
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { throw new ACDError("not UTF-8"); }
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i++; };
  const fail = (m) => { throw new ACDError(`not JSON: ${m} at ${i}`); };
  function value() {
    ws();
    const c = text[i];
    if (c === "{") return object();
    if (c === "[") return array();
    if (c === '"') return string();
    if (c === "-" || (c >= "0" && c <= "9")) return number();
    for (const [lit, v] of [["true", true], ["false", false], ["null", null]]) {
      if (text.startsWith(lit, i)) { i += lit.length; return v; }
    }
    return fail("unexpected character");
  }
  function object() {
    i++; const out = new Map(); ws();
    if (text[i] === "}") { i++; return out; }
    for (;;) {
      ws(); if (text[i] !== '"') fail("expected a member name");
      const k = string(); ws(); if (text[i] !== ":") fail("expected ':'"); i++;
      if (out.has(k)) throw new ACDError(`duplicate member name ${JSON.stringify(k)}`);
      out.set(k, value()); ws();
      if (text[i] === ",") { i++; continue; }
      if (text[i] === "}") { i++; return out; }
      fail("expected ',' or '}'");
    }
  }
  function array() {
    i++; const out = []; ws();
    if (text[i] === "]") { i++; return out; }
    for (;;) {
      out.push(value()); ws();
      if (text[i] === ",") { i++; continue; }
      if (text[i] === "]") { i++; return out; }
      fail("expected ',' or ']'");
    }
  }
  function string() {
    i++; let s = "";
    for (;;) {
      const c = text[i];
      if (c === undefined) fail("unterminated string");
      if (c === '"') { i++; break; }
      if (c.charCodeAt(0) < 0x20) fail("control character in a string");
      if (c === "\\") {
        const e = text[i + 1];
        const simple = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
        if (e in simple) { s += simple[e]; i += 2; continue; }
        if (e === "u" && /^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) {
          s += String.fromCharCode(parseInt(text.slice(i + 2, i + 6), 16)); i += 6; continue;
        }
        fail("bad escape");
      }
      s += c; i++;
    }
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(s)) {
      throw new ACDError("unpaired surrogate in a string");
    }
    return s;
  }
  function number() {
    const m = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/.exec(text.slice(i));
    if (!m) fail("bad number");
    i += m[0].length;
    return { [NUM]: m[0] };
  }
  const v = value(); ws();
  if (i !== text.length) fail("trailing content");
  return v;
}

const isObj = (v) => v instanceof Map;

function references(v, out) {
  if (isObj(v)) {
    for (const [k, x] of v) {
      if (k === "reference" && typeof x === "string") out.add(x); else references(x, out);
    }
  } else if (Array.isArray(v)) v.forEach((x) => references(x, out));
}

function transform(b) {
  if (!isObj(b) || b.get("resourceType") !== "Bundle") throw new ACDError("not a Bundle");
  const entries = b.get("entry");
  const first = Array.isArray(entries) && entries.length && isObj(entries[0]) ? entries[0].get("resource") : null;
  if (!isObj(first) || first.get("resourceType") !== "Composition") throw new ACDError("the first entry is not a Composition");
  const out = new Map([...b].filter(([k]) => !["id", "identifier", "timestamp"].includes(k)));
  const comp = new Map(first);
  const parties = new Set();
  for (const a of comp.get("attester") || []) {
    const p = isObj(a) && isObj(a.get("party")) ? a.get("party").get("reference") : undefined;
    if (typeof p === "string") parties.add(p);
  }
  comp.delete("attester");
  const head = new Map(entries[0]); head.set("resource", comp);
  const rest = [head, ...entries.slice(1)];
  const candidates = new Set(rest.slice(1).filter((e) => isObj(e) && parties.has(e.get("fullUrl"))));
  const kept = rest.filter((e) => !candidates.has(e));
  const refs = new Set();
  const probe = new Map(out); probe.set("entry", kept);
  references(probe, refs);
  out.set("entry", rest.filter((e) => !candidates.has(e) || refs.has(e.get("fullUrl"))));
  return out;
}

function str(s) {
  let o = '"';
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (ch === '"') o += '\\"';
    else if (ch === "\\") o += "\\\\";
    else if (ch === "\b") o += "\\b";
    else if (ch === "\f") o += "\\f";
    else if (ch === "\n") o += "\\n";
    else if (ch === "\r") o += "\\r";
    else if (ch === "\t") o += "\\t";
    else if (c < 0x20) o += "\\u" + c.toString(16).padStart(4, "0");
    else o += ch;
  }
  return o + '"';
}

function serialise(v) {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v === null) return "null";
  if (typeof v === "string") return str(v);
  if (Array.isArray(v)) return "[" + v.map(serialise).join(",") + "]";
  if (isObj(v)) {
    const keys = [...v.keys()].sort();            // UTF-16 code unit order
    return "{" + keys.map((k) => str(k) + ":" + serialise(v.get(k))).join(",") + "}";
  }
  if (v && v[NUM] !== undefined) return v[NUM];
  throw new ACDError("unexpected value");
}

export function canonical(bytes) { return Buffer.from(serialise(transform(parse(bytes))), "utf8"); }
export function digest(bytes) { return createHash("sha256").update(canonical(bytes)).digest("hex"); }

if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop())) {
  const file = process.argv.find((a, n) => n > 1 && !a.startsWith("--"));
  try {
    const data = readFileSync(file);
    if (process.argv.includes("--canonical")) process.stdout.write(canonical(data));
    else console.log(digest(data));
  } catch (e) {
    if (e instanceof ACDError) { console.error(`rejected: ${e.message}`); process.exit(2); }
    throw e;
  }
}
