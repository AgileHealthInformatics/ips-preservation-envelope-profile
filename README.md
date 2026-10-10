# IPS Preservation Envelope Profile

*Preserve the clinical document. Exchange the computable summary.*

An unofficial draft profile for issuing a patient summary as two representations of one clinical statement: a
PDF/A-3 **preservation envelope** whose pages are rendered from, and which embed, the exact octets of an HL7 FHIR
International Patient Summary; and the same octets registered and exchanged as a document in their own right.

- **Version:** 0.5.0 (2026-10-09) · **Maturity:** experimental draft, not an implementation candidate · **Canonical identifier:** `urn:oid:2.25.11064312502901710892401295388462879468`
- **Read it:** [`index.html`](index.html) - the static publication, self-contained, usable offline and with JavaScript
  disabled
- **Status:** unofficial draft by an individual. Not a standard; not endorsed by HL7, IHE, ISO or the European
  Commission; not evidence of conformance with the EHDS Regulation, Implementing Regulation (EU) 2026/2083 or
  MyHealth@EU.

## What is in this repository

| File | Role |
|---|---|
| `ips-preservation-envelope.respec.html` | The maintained authoring source (ReSpec). Edit this. |
| `index.html` | Generated static publication. Do not edit by hand. |
| `rules.json` | Generated machine-readable rule catalogue: every rule and its normative obligations (`RULE` or `RULE.a`, `RULE.b`…), each with its requirement level, classes, option, verification methods and their class scopes, tests, history and text (rule CHK-09) |
| `dependencies.json` | Generated from the normative dependency lock: exact versions of FHIR, IPS, ITI TF, XDS Metadata Update, MHD, sIPS, the format-code vocabulary, PDF/A and the validators, the dependency statuses and the (currently empty) list of permitted alternatives (rule CON-02) |
| `release-manifest.json` | Generated: SHA-256 of the source, every generated file, the ACD-1 files and the StructureDefinitions, the tool versions used, and the results of the release checks |
| `acd/` | Attested content digest, algorithm ACD-1: a Python and an independent JavaScript implementation, cross-language input vectors and their expected results |
| `fhir/` | StructureDefinitions of the profile's two MHD extensions (lifecycle state; withdrawal, a modifier extension). Provisional canonical base; not yet validated |
| `build.py` | Renders the source with ReSpec, lints the normative obligations, writes the generated files, checks the static page offline, re-derives the catalogue independently and, with `--check-reproducible`, builds twice |
| `.github/workflows/build.yml` | CI: pinned ReSpec and Chromium, reproducible build, and a check that the committed generated files are the build outputs |
| `static.css` | Stylesheet inlined into `index.html` |

## Structure

| Layer | Contents |
|---|---|
| Part I - Core | Structured source, provenance and assurance, rendition fidelity, envelope, lifecycle, preservation |
| Part II - Content binding | HL7 FHIR IPS 2.0.1: identifiers, provenance, assurance wording, coverage matrix (R/E/N), canonical text forms, provenance and time mapping, attestation sequence |
| Part III - Exchange binding | IHE XDS.b, XDS Metadata Update (ITI-57), MHD 4.2.4, sIPS, XCA, XCPD: actor bindings, metadata, lifecycle state mapping across layers, identity mapping, lifecycle operations |
| Annexes | Conformance kit (Checker rules, verification methods, vectors, live scenarios), verification matrix, rule index and demonstrator status, normative change log, review dispositions, issue register, source register, version history |

Version 0.5.0 responds to a reassessment of 0.4.0 (items F-040-01 to F-040-07): every normative obligation now
carries exactly one requirement keyword, or declares acceptance conditions with one keyword each (128 obligations
become 215); every mandatory obligation without a test declares its verification route; withdrawal is an explicit,
evidenced issuer event and is no longer inferred from XDS deprecation and a missing replacement, and MHD carries it
in a modifier extension with a canonical URL; `Organization.partOf` is no longer used as provenance; the attested
content digest is a complete algorithm (ACD-1) with cross-language vectors; and the timestamps are required only when
their events occur. It is published as an experimental draft. Every changed rule carries its history, and the
normative change log links each change to its reason and to the tests that verify it.

## Implementation

The open-source demonstrator *EHDS Structured Clinical Document PoC* (release 0.5.0) implements profile **0.4.0** on
synthetic data, with a catalogue-driven Checker, byte-reproducible vectors and live scenarios. Its status against
0.5.0 is recorded rule by rule in the rule index annex; the main gaps are listed as issue IR-15 (results keyed to 0.4.0
obligation identifiers, no support for acceptance conditions, withdrawal inferred from a missing RPLC successor, two
ACD-1 vectors failed). That table is the project's own tracker, not independent conformance evidence.

## Building

```bash
npm pack respec@37.4.1 && tar xzf respec-37.4.1.tgz     # ReSpec, for an offline build
pip install playwright                                   # with a Chromium available to Playwright
                                                         # Node 18 or later runs the JavaScript ACD-1 implementation
python build.py --respec package/builds/respec-w3c.js --check-reproducible
#   writes index.html, rules.json, dependencies.json, release-manifest.json
```

The build fails on any ReSpec error or warning, on any external resource in the output, on a requirement keyword
outside a declared obligation or disagreeing with its level, on an obligation with more than one keyword and no
acceptance conditions, on an obligation without a verification method, on a mandatory obligation with neither a test
nor a declared verification route, on a reference to an undefined test, if the
independently re-derived catalogue differs, if the two ACD-1 implementations disagree with each other or with
`acd/expected.json`, if a second build is not byte-identical, and if the static page does not work offline with JavaScript disabled (table of contents,
bibliography, rule index, verification matrix, change log and every internal link are checked with scripting off and
the network blocked).

## Contributing

Open an issue citing the rule or obligation identifier (for example `MET-08` or `REN-04.b`). Rule identifiers are never reused. A change to the
meaning of a rule increments the minor version, is recorded in the rule's `data-history`, and requires the tests and
vectors listed in its `data-test` to be reviewed in the same release.

## Licence

Specification text: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Build script and stylesheet: Apache-2.0. ISO, HL7 and IHE specifications are referenced, not reproduced.
