#!/usr/bin/env python3
"""Build and verify a release of the profile from its maintained ReSpec authoring source.

    python build.py --respec path/to/respec-w3c.js [--check-reproducible]

Input:   ips-preservation-envelope.respec.html  (authoring source; rendered by ReSpec in a browser)
Outputs: index.html             self-contained static rendition (no scripts, no external resources)
         rules.json             rule catalogue: rules and their normative obligations (profile CHK-09)
         dependencies.json      dependency manifest from the "Dependency lock" table (CON-02)
         release-manifest.json  SHA-256 of the source, every generated file and the build tool versions

Verification (profile Annex "Release artefacts"); the build exits non-zero on any failure:
  1. ReSpec reports no error or warning.
  2. Obligation lint: every requirement keyword sits inside a declared obligation; an obligation contains exactly one
     requirement keyword, or declares acceptance conditions that contain one keyword each and leave none outside;
     keywords agree with the declared level; levels are MUST, MUST NOT, SHOULD or SHOULD NOT; every obligation has a
     known verification method; a mandatory obligation without a test declares its verification route (declaration,
     inspection or a recorded kit gap) consistently with its methods; obligation ids are unique; every test reference
     names a vector or scenario defined in the document; no requirement keyword appears in normative prose outside a
     rule.
  3. The static page works offline with JavaScript disabled and the network blocked: table of contents,
     bibliography, rule index, verification matrix and change log populated; every internal link resolves.
  4. Basic accessibility: document language, text alternatives for figures and images, table headers, heading order.
  5. rules.json and dependencies.json are re-derived from the static index.html by an independent Python parser and
     compared field by field.
  6. Attested content digest (ACD-1): the Python and JavaScript implementations in acd/ agree with acd/expected.json
     and with each other on every vector, and the worked example and vector table in the page match them.
  7. With --check-reproducible, a second build must produce byte-identical outputs.

Needs Playwright with Chromium. ReSpec is loaded from a local file (npm package ``respec``,
builds/respec-w3c.js) so the build itself works offline.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import json
import platform
import re
import shutil
import sys
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SRC = HERE / "ips-preservation-envelope.respec.html"
OUT = HERE / "index.html"
RULES = HERE / "rules.json"
DEPS = HERE / "dependencies.json"
MANIFEST = HERE / "release-manifest.json"
CSS = (HERE / "static.css").read_text(encoding="utf-8")
CANONICAL_ID = "urn:oid:2.25.11064312502901710892401295388462879468"
LEVELS = {"MUST", "MUST NOT", "SHOULD", "SHOULD NOT"}
METHODS = {"artefact", "live", "claim", "inspection", "vectors"}
ROUTES = {"declaration": "claim", "inspection": "inspection", "kit-gap": None}
KW = re.compile(r"\b(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY)\b")

# Runs in the ReSpec-processed page, before scripts are removed: obligations() is the page's own function, so the
# catalogue and the generated annexes come from one definition.
EXTRACT = """() => {
  const split = (s) => (s || '').split(/[\\s,]+/).filter(Boolean);
  const classes = (s) => (s || '').split(',').map((x) => x.trim()).filter(Boolean);
  const text = (el) => el.textContent.replace(/\\s+/g, ' ').trim();
  const rules = [...document.querySelectorAll('div.rule')].map((r) => {
    const p = r.querySelector('p');
    const spans = [...r.querySelectorAll('span.ob')];
    const clone = p.cloneNode(true);
    clone.querySelectorAll('span.ob').forEach((sp) => sp.remove());
    const layer = r.closest('[data-layer]');
    const section = r.closest('section');
    const obs = obligations(r).map((o) => {
      const outside = o.el.cloneNode(true);
      outside.querySelectorAll('span.ac').forEach((x) => x.remove());
      return {
        id: o.id, level: o.level, mandatory: o.level === 'MUST' || o.level === 'MUST NOT',
        classes: classes(o.classes), option: o.option || null, verification: split(o.verify), tests: split(o.test),
        evidence: o.evidence || null, route: route(o),
        conditions: o.conditions.map((c) => ({ id: c.id, text: text(c.el) })),
        text: text(o.el), textOutsideConditions: o.conditions.length ? text(outside) : null,
      };
    });
    return {
      id: r.id, layer: layer ? layer.dataset.layer : null, section: section ? section.id : null,
      classes: classes(r.dataset.applies), option: r.dataset.option || null,
      history: (r.dataset.history || '').split(';').map((s) => s.trim()).filter(Boolean)
        .map((h) => { const [release, reason] = h.split(/\\s+/); return { release, reason }; }),
      status: r.classList.contains('new') ? 'new' : r.classList.contains('changed') ? 'changed' : 'unchanged',
      text: text(p), textOutsideObligations: spans.length ? text(clone) : '', obligations: obs,
    };
  });
  const deps = [...document.querySelectorAll('#dependency-table tbody tr')].map((tr) => {
    const td = [...tr.querySelectorAll('td')].map((c) => c.textContent.replace(/\\s+/g, ' ').trim());
    return { key: tr.dataset.key, specification: td[1], version: td[2], identifier: td[3], status: td[4], notes: td[5] };
  });
  const tests = [...document.querySelectorAll('tr[id^="vec-"], tr[id^="live-"], tr[id^="acd-ACD-"]')].map((t) => t.id.replace(/^(vec|live|acd)-/, ''));
  // Requirement keywords in normative prose outside rules.
  const skip = 'div.rule, .informative, section.informative, .note, .issue, .example, code, pre, #references, #toc, ' +
               '#index, #sotd, #abstract, .head, #obligations, table, #review-disposition, #issue-register, #source-register, #changes';
  const stray = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode;
    if (n.parentElement && n.parentElement.closest(skip)) continue;
    const para = n.parentElement && n.parentElement.closest('p');
    if (para && /BCP 14/.test(para.textContent)) continue;   // ReSpec's RFC 2119 / RFC 8174 boilerplate
    const m = n.textContent.match(/\\b(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY)\\b/);
    if (m) stray.push(`${(n.parentElement.closest('section') || {}).id || '?'}: ${n.textContent.trim().slice(0, 80)}`);
  }
  return { rules, deps, tests, stray };
}"""


def render(respec: str) -> tuple[str, list, list, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(respec, tmp / "respec-w3c.js")
        for extra in Path(respec).parent.glob("respec-highlight*.js"):
            shutil.copy(extra, tmp / extra.name)
        html = SRC.read_text(encoding="utf-8").replace("https://www.w3.org/Tools/respec/respec-w3c", "respec-w3c.js")
        (tmp / "index.html").write_text(html, encoding="utf-8")
        handler = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(tmp)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html")
                page.wait_for_function("document.respec && document.respec.ready", timeout=60000)
                page.evaluate("document.respec.ready")
                errors = page.evaluate("(document.respec.errors||[]).map(e=>e.message||String(e))")
                warnings = page.evaluate("(document.respec.warnings||[]).map(e=>e.message||String(e))")
                data = page.evaluate(EXTRACT)
                out = page.evaluate("document.respec.toHTML()")
                data["chromium"] = browser.version
                browser.close()
        finally:
            srv.shutdown()
    return out, errors, warnings, data


def make_static(out: str) -> str:
    out = re.sub(r'<link[^>]+href="https://www\.w3\.org/[^"]*"[^>]*>', "", out)
    out = re.sub(r'<script[^>]+src="https://www\.w3\.org/[^"]*"[^>]*>\s*</script>', "", out)
    out = re.sub(r'<img[^>]+src="https://www\.w3\.org/[^"]*"[^>]*>', "", out)
    out = re.sub(r"<script\b[^>]*>.*?</script>", "", out, flags=re.S)
    return out.replace("</head>", f"<style>{CSS}</style></head>", 1)


# ------------------------------------------------------------------ obligation lint

def lint(data: dict) -> list[str]:
    problems: list[str] = []
    known_tests = set(data["tests"])
    seen: set[str] = set()
    for r in data["rules"]:
        if KW.search(r["textOutsideObligations"]):
            problems.append(f"{r['id']}: requirement keyword outside a declared obligation")
        for o in r["obligations"]:
            if o["id"] in seen:
                problems.append(f"{o['id']}: duplicate obligation id")
            seen.add(o["id"])
            if o["level"] not in LEVELS:
                problems.append(f"{o['id']}: level {o['level']!r} is not MUST, MUST NOT, SHOULD or SHOULD NOT")
                continue
            occ = KW.findall(o["text"])
            kws = set(occ)
            if not kws:
                problems.append(f"{o['id']}: no requirement keyword in the obligation text")
            if o["conditions"]:
                if KW.search(o["textOutsideConditions"] or ""):
                    problems.append(f"{o['id']}: requirement keyword outside its acceptance conditions")
                for c in o["conditions"]:
                    n = len(KW.findall(c["text"]))
                    if n != 1:
                        problems.append(f"{c['id']}: an acceptance condition has {n} requirement keywords, not one")
                if len(o["conditions"]) < 2:
                    problems.append(f"{o['id']}: a single acceptance condition; state it as the obligation")
            elif len(occ) > 1:
                problems.append(f"{o['id']}: {len(occ)} requirement keywords {occ}; split the obligation or declare "
                                "its acceptance conditions")
            mand = o["level"].startswith("MUST")
            if "MAY" in kws or any(k.startswith("MUST") != mand for k in kws):
                problems.append(f"{o['id']}: keywords {sorted(kws)} disagree with declared level {o['level']}")
            if not o["conditions"] and len(kws) == 1 and next(iter(kws)) != o["level"]:
                problems.append(f"{o['id']}: keyword {next(iter(kws))} disagrees with declared level {o['level']}")
            if not o["verification"]:
                problems.append(f"{o['id']}: no verification method")
            if set(o["verification"]) - METHODS:
                problems.append(f"{o['id']}: unknown method {sorted(set(o['verification']) - METHODS)}")
            if not o["classes"]:
                problems.append(f"{o['id']}: no conformance class")
            unknown = [t for t in o["tests"] if t not in known_tests]
            if unknown:
                problems.append(f"{o['id']}: unknown test references {unknown}")
            rt = o["route"]["kind"]
            if o["tests"] and rt != "tests":
                problems.append(f"{o['id']}: route {rt!r} declared although the obligation has tests")
            if not o["tests"] and rt != "none":
                if rt not in ROUTES:
                    problems.append(f"{o['id']}: unknown verification route {rt!r}")
                elif ROUTES[rt] and ROUTES[rt] not in o["verification"]:
                    problems.append(f"{o['id']}: route {rt} needs the {ROUTES[rt]} method")
                elif not o["route"]["detail"]:
                    problems.append(f"{o['id']}: route {rt} without the detail of what is declared or inspected")
            if o["mandatory"] and not o["tests"] and rt == "none":
                problems.append(f"{o['id']}: mandatory obligation with no test and no declared verification route")
    for st in data["stray"]:
        problems.append(f"requirement keyword in normative prose outside a rule: {st}")
    return problems


# ------------------------------------------------------------------ offline + accessibility checks

def offline_check(path: Path, n_rules: int, n_obl: int) -> list[str]:
    problems: list[str] = []
    requests: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False, offline=True)
        page = ctx.new_page()
        page.on("request", lambda r: requests.append(r.url) if not r.url.startswith("file:") else None)
        page.goto(path.as_uri())
        r = page.evaluate("""() => {
          const ids = new Set([...document.querySelectorAll('[id]')].map(e => e.id));
          const internal = [...document.querySelectorAll('a[href^="#"]')].map(a => a.getAttribute('href').slice(1));
          const broken = [...new Set(internal.filter(h => h && !ids.has(decodeURIComponent(h))))];
          const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => +h.tagName[1]);
          let skips = 0; for (let i = 1; i < heads.length; i++) if (heads[i] > heads[i-1] + 1) skips++;
          return { broken, toc: document.querySelectorAll('#toc a[href^="#"]').length,
            biblio: document.querySelectorAll('#references dt, #normative-references dt, #informative-references dt').length,
            scripts: document.scripts.length,
            index: document.querySelectorAll('#rule-table-body tr').length,
            verification: document.querySelectorAll('#verification-table-body tr').length,
            changes: document.querySelectorAll('#change-table-body tr').length,
            lang: document.documentElement.lang,
            imgNoAlt: [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt')).length,
            svgNoName: [...document.querySelectorAll('svg')].filter(s => !s.querySelector('title') &&
                       !s.getAttribute('aria-label') && s.getAttribute('aria-hidden') !== 'true' &&
                       !s.closest('a,button')).length,
            figNoCaption: [...document.querySelectorAll('figure')].filter(f => !f.querySelector('figcaption')).length,
            tablesNoHeader: [...document.querySelectorAll('table')].filter(t => !t.querySelector('th')).length,
            emptyLinks: [...document.querySelectorAll('a[href]')].filter(a => !a.textContent.trim() &&
                        !a.getAttribute('aria-label') && !a.querySelector('img[alt]')).length,
            headingSkips: skips };
        }""")
        browser.close()
    checks = [
        (not r["broken"], f"broken internal links: {r['broken'][:10]}"),
        (r["toc"] >= 20, f"table of contents has only {r['toc']} links"),
        (r["biblio"] >= 10, f"bibliography has only {r['biblio']} entries"),
        (r["index"] == n_rules, f"rule index has {r['index']} rows for {n_rules} rules"),
        (r["verification"] == n_obl, f"verification matrix has {r['verification']} rows for {n_obl} obligations"),
        (r["changes"] > 0, "normative change log is empty"),
        (not r["scripts"], f"{r['scripts']} script element(s) in the static output"),
        (not requests, f"network requests attempted: {requests[:5]}"),
        (r["lang"].startswith("en"), f"document language {r['lang']!r}"),
        (not r["imgNoAlt"], f"{r['imgNoAlt']} image(s) without alt"),
        (not r["svgNoName"], f"{r['svgNoName']} svg(s) without an accessible name"),
        (not r["figNoCaption"], f"{r['figNoCaption']} figure(s) without a caption"),
        (not r["tablesNoHeader"], f"{r['tablesNoHeader']} table(s) without header cells"),
        (not r["emptyLinks"], f"{r['emptyLinks']} link(s) without text"),
        (not r["headingSkips"], f"{r['headingSkips']} heading level skip(s)"),
    ]
    problems += [msg for ok, msg in checks if not ok]
    print(f"offline/accessibility check (JavaScript disabled, network blocked): toc={r['toc']} biblio={r['biblio']} "
          f"rules={r['index']} obligations={r['verification']} changes={r['changes']} broken={len(r['broken'])} "
          f"requests={len(requests)} problems={len(problems)}")
    return problems


# ------------------------------------------------------------------ independent re-derivation from index.html

class _Static(HTMLParser):
    """Minimal parser over the static page: rule blocks, obligation spans and the dependency table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rules: list[dict] = []
        self.deps: list[dict] = []
        self._stack: list[tuple[str, dict]] = []
        self._rule: dict | None = None
        self._ob: dict | None = None
        self._depth = 0
        self._ob_depth = 0
        self._in_dep = False
        self._dep_row: dict | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and "rule" in (a.get("class") or "").split() and a.get("id"):
            self._rule = {"attrs": a, "text": [], "obs": []}
            self._depth = 1
            return
        if self._rule is not None:
            if tag == "div":
                self._depth += 1
            if tag == "span" and "ac" in (a.get("class") or "").split():
                (self._ob or self._rule).setdefault("acs", []).append(a.get("data-ac"))
            if tag == "span" and "ob" in (a.get("class") or "").split():
                self._ob = {"attrs": a, "text": []}
                self._ob_depth = 1
            elif self._ob is not None and tag == "span":
                self._ob_depth += 1
        if tag == "table" and a.get("id") == "dependency-table":
            self._in_dep = True
        if self._in_dep and tag == "tr" and a.get("data-key"):
            self._dep_row = {"key": a["data-key"], "cells": []}
        if self._dep_row is not None and tag == "td":
            self._cell = []

    def handle_endtag(self, tag):
        if self._rule is not None:
            if tag == "span" and self._ob is not None:
                self._ob_depth -= 1
                if self._ob_depth == 0:
                    self._rule["obs"].append(self._ob)
                    self._ob = None
            if tag == "div":
                self._depth -= 1
                if self._depth == 0:
                    self.rules.append(self._rule)
                    self._rule = None
        if tag == "td" and self._cell is not None:
            self._dep_row["cells"].append(" ".join("".join(self._cell).split()))
            self._cell = None
        if tag == "tr" and self._dep_row is not None:
            self.deps.append(self._dep_row)
            self._dep_row = None
        if tag == "table" and self._in_dep:
            self._in_dep = False

    def handle_data(self, data):
        if self._rule is not None:
            self._rule["text"].append(data)
            if self._ob is not None:
                self._ob["text"].append(data)
        if self._cell is not None:
            self._cell.append(data)


def verify_against_static(path: Path, rules: list[dict], deps: list[dict]) -> list[str]:
    p = _Static()
    p.feed(path.read_text(encoding="utf-8"))
    split = lambda s: [x for x in re.split(r"[\s,]+", s or "") if x]  # noqa: E731
    cls = lambda s: [x.strip() for x in (s or "").split(",") if x.strip()]  # noqa: E731
    derived = {}
    for r in p.rules:
        a = r["attrs"]
        base = {"classes": cls(a.get("data-applies")), "option": a.get("data-option") or None,
                "verification": split(a.get("data-verify")), "tests": split(a.get("data-test"))}
        rk = lambda t, rt: "tests" if t else ((rt or "none").split(":")[0].strip() or "none")  # noqa: E731
        if not r["obs"]:
            derived[a["id"]] = {"level": a.get("data-level"), **base,
                                "route": rk(base["tests"], a.get("data-route")),
                                "conditions": [f"{a['id']}/{n}" for n in r.get("acs", [])]}
        for ob in r["obs"]:
            o = ob["attrs"]
            oid = f"{a['id']}.{o['data-ob']}"
            tests = split(o["data-test"]) if "data-test" in o else base["tests"]
            derived[oid] = {
                "level": o.get("data-level"),
                "classes": cls(o["data-applies"]) if "data-applies" in o else base["classes"],
                "option": (o.get("data-option") or None) if "data-option" in o else base["option"],
                "verification": split(o["data-verify"]) if "data-verify" in o else base["verification"],
                "tests": tests,
                "route": rk(tests, o["data-route"] if "data-route" in o else a.get("data-route")),
                "conditions": [f"{oid}/{n}" for n in ob.get("acs", [])]}
    catalogue = {o["id"]: {**{k: o[k] for k in ("level", "classes", "option", "verification", "tests")},
                           "route": o["route"]["kind"], "conditions": [c["id"] for c in o["conditions"]]}
                 for r in rules for o in r["obligations"]}
    problems = []
    if set(derived) != set(catalogue):
        problems.append(f"obligation ids differ: static-only {sorted(set(derived) - set(catalogue))[:5]}, "
                        f"catalogue-only {sorted(set(catalogue) - set(derived))[:5]}")
    for k in sorted(set(derived) & set(catalogue)):
        if derived[k] != catalogue[k]:
            problems.append(f"{k}: static {derived[k]} != catalogue {catalogue[k]}")
    static_deps = {d["key"]: d["cells"][2] for d in p.deps}
    cat_deps = {d["key"]: d["version"] for d in deps}
    if static_deps != cat_deps:
        problems.append(f"dependency versions differ: static {static_deps} catalogue {cat_deps}")
    print(f"independent re-derivation from {path.name}: {len(derived)} obligations, {len(static_deps)} dependencies, "
          f"{len(problems)} difference(s)")
    return problems


# ------------------------------------------------------------------ attested content digest (ACD-1)

def acd_check(static_path: Path) -> tuple[list[str], str]:
    import subprocess
    sys.path.insert(0, str(HERE / "acd"))
    import acd  # noqa: PLC0415
    problems: list[str] = []
    expected = json.loads((HERE / "acd" / "expected.json").read_text(encoding="utf-8"))["vectors"]
    node = shutil.which("node")
    node_version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip() if node else ""
    if not node:
        problems.append("ACD-1: node not found; the JavaScript implementation cannot be run")
    for vid, v in expected.items():
        data = (HERE / "acd" / v["input"]).read_bytes()
        try:
            py, py_c = acd.digest(data), acd.canonical(data)
        except acd.ACDError:
            py, py_c = "rejected", b""
        js, js_c = "rejected", b""
        if node:
            r = subprocess.run([node, str(HERE / "acd" / "acd.mjs"), str(HERE / "acd" / v["input"])],
                               capture_output=True, text=True)
            if r.returncode == 0:
                js = r.stdout.strip()
                js_c = subprocess.run([node, str(HERE / "acd" / "acd.mjs"), str(HERE / "acd" / v["input"]),
                                       "--canonical"], capture_output=True).stdout
            elif r.returncode != 2:
                problems.append(f"ACD-1 {vid}: JavaScript implementation failed: {r.stderr[-200:]}")
        want = v["digest"] if v["expect"] == "digest" else "rejected"
        if py != want:
            problems.append(f"ACD-1 {vid}: Python {py} != expected {want}")
        if node and js != want:
            problems.append(f"ACD-1 {vid}: JavaScript {js} != expected {want}")
        if node and py_c != js_c:
            problems.append(f"ACD-1 {vid}: canonical forms differ between implementations")
        if v.get("sameAs") and expected[v["sameAs"]].get("digest") != v.get("digest"):
            problems.append(f"ACD-1 {vid}: not the same digest as {v['sameAs']}")
    page = static_path.read_text(encoding="utf-8")
    import html as _html
    m = re.search(r'<pre[^>]*id="acd-example-input"[^>]*>(.*?)</pre>', page, re.S)
    if not m or _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() != \
            (HERE / "acd" / "vectors" / "ACD-01.json").read_text(encoding="utf-8").strip():
        problems.append("ACD-1: the worked example input in the page is not vector ACD-01")
    m = re.search(r'<pre[^>]*id="acd-example-canonical"[^>]*>(.*?)</pre>', page, re.S)
    canon = acd.canonical((HERE / "acd" / "vectors" / "ACD-01.json").read_bytes()).decode("utf-8")
    if not m or _html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() != canon:
        problems.append("ACD-1: the worked example canonical form in the page differs from the implementation")
    m = re.search(r'id="acd-example-digest"[^>]*>([0-9a-f]{64})<', page)
    if not m or m.group(1) != expected["ACD-01"]["digest"]:
        problems.append("ACD-1: the worked example digest in the page is wrong")
    rows = dict(re.findall(r'<tr id="acd-(ACD-\d+)"[^>]*data-digest="([0-9a-f]*)"', page))
    for vid, v in expected.items():
        if rows.get(vid, None) != v.get("digest", ""):
            problems.append(f"ACD-1: vector table row {vid} does not match expected.json")
    print(f"ACD-1: {len(expected)} vectors; Python and JavaScript ({node_version or 'not run'}) agree: "
          f"{not problems}")
    return problems, node_version


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(respec: str) -> tuple[list[str], dict]:
    out, errors, warnings, data = render(respec)
    for m in errors + warnings:
        print("  ReSpec:", m)
    problems = [f"ReSpec: {m}" for m in errors + warnings]
    static = make_static(out)
    OUT.write_text(static, encoding="utf-8")
    ext = re.findall(r'<(?:link|script|img)[^>]+(?:src|href)="(https?://[^"]+)"', static)
    if ext:
        problems.append(f"external resources: {ext}")
    version = re.search(r"Version (\d+\.\d+\.\d+)", SRC.read_text(encoding="utf-8")).group(1)
    problems += lint(data)
    rules = data["rules"]
    for r in rules:
        r.pop("textOutsideObligations", None)
        for o in r["obligations"]:
            o.pop("textOutsideConditions", None)
    n_obl = sum(len(r["obligations"]) for r in rules)
    catalogue = {"profile": CANONICAL_ID, "profileVersion": version, "generatedFrom": SRC.name,
                 "classes": ["Issuer", "Envelope", "Publisher", "Responder", "Responding Gateway", "Receiver",
                             "Repository", "Checker"],
                 "options": {"AI": "Attested Issuance", "PP": "Protected Preservation", "MHD": "MHD Façade",
                             "OD": "On-Demand"},
                 "levels": {"mandatory": ["MUST", "MUST NOT"], "advisory": ["SHOULD", "SHOULD NOT"]},
                 "verificationMethods": sorted(METHODS),
                 "verificationRoutes": {"tests": "verified by the tests listed",
                                        "declaration": "a declaration in the conformance claim, checked for presence",
                                        "inspection": "a declared inspection, reported as declared (CHK-08)",
                                        "kit-gap": "no test in this kit; recorded gap"},
                 "methodScope": {"artefact": ["Envelope", "Issuer"],
                                 "live": ["Issuer", "Publisher", "Responder", "Responding Gateway", "Receiver",
                                          "Repository"],
                                 "claim": ["*"], "vectors": ["Checker"], "inspection": ["fallback"]},
                 "tests": sorted(set(data["tests"])),
                 "rules": rules}
    RULES.write_text(json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    DEPS.write_text(json.dumps({"profile": CANONICAL_ID, "profileVersion": version, "dependencies": data["deps"],
                                "statuses": ["exact", "permitted-alternative", "unsupported-deviation"],
                                "permittedAlternatives": []}, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    problems += offline_check(OUT, len(rules), n_obl)
    problems += verify_against_static(OUT, rules, data["deps"])
    acd_problems, node_version = acd_check(OUT)
    problems += acd_problems
    for sd in sorted((HERE / "fhir").glob("StructureDefinition-*.json")):
        url = json.loads(sd.read_text(encoding="utf-8"))["url"]
        if url not in static:
            problems.append(f"{sd.name}: its url {url} is not stated in the page")
    respec_pkg = Path(respec).resolve().parent.parent / "package.json"
    manifest = {
        "profile": CANONICAL_ID, "profileVersion": version,
        "files": {str(f.relative_to(HERE)): sha(f) for f in
                  (SRC, OUT, RULES, DEPS, HERE / "static.css", HERE / "build.py",
                   *sorted((HERE / "acd").rglob("*.*")), *sorted((HERE / "fhir").glob("*.json")))
                  if "__pycache__" not in f.parts},
        "tools": {"respec": json.loads(respec_pkg.read_text())["version"] if respec_pkg.is_file() else "unknown",
                  "chromium": data["chromium"], "python": platform.python_version(), "node": node_version},
        "checks": {"respec": "no errors or warnings", "obligationLint": "passed", "offline": "passed",
                   "accessibility": "basic checks passed", "catalogueReDerivation": "passed",
                   "attestedContentDigest": "Python and JavaScript implementations agree on every vector"},
    }
    print(f"wrote {OUT.name} ({len(static)} bytes), {RULES.name} ({len(rules)} rules, {n_obl} obligations, sha256 "
          f"{sha(RULES)[:16]}…), {DEPS.name} ({len(data['deps'])} entries)")
    return problems, manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--respec", required=True, help="path to a local respec-w3c.js build")
    ap.add_argument("--check-reproducible", action="store_true", help="build twice and compare outputs")
    args = ap.parse_args()
    problems, manifest = build(args.respec)
    if args.check_reproducible and not problems:
        first = {n: sha(HERE / n) for n in ("index.html", "rules.json", "dependencies.json")}
        problems2, _ = build(args.respec)
        second = {n: sha(HERE / n) for n in first}
        diff = [n for n in first if first[n] != second[n]]
        problems += problems2 + ([f"not reproducible: {diff}"] if diff else [])
        manifest["checks"]["reproducible"] = "passed" if not diff else "failed"
    for pr in problems:
        print("  PROBLEM:", pr)
    if problems:
        raise SystemExit(1)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.name}; all release checks passed")


if __name__ == "__main__":
    sys.exit(main())
