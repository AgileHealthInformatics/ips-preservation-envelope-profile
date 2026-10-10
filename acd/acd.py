#!/usr/bin/env python3
"""Attested content digest, algorithm ACD-1 (IPS Preservation Envelope Profile, "Attested content digest").

Reference implementation in Python. An independent implementation in JavaScript is acd.mjs; the profile build runs
both over the vectors in vectors/ and requires them to agree with expected.json.

    python acd.py FILE.json            print the digest (lowercase hex SHA-256)
    python acd.py --canonical FILE     print the canonical form (UTF-8)

Exit status 2 and a message on stderr when the input is rejected (step 1).
"""
from __future__ import annotations

import hashlib
import json
import sys


class ACDError(ValueError):
    pass


class Number(str):
    """A JSON number kept as its exact lexical form from the input (step 1)."""


def _pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ACDError(f"duplicate member name {k!r}")
        out[k] = v
    return out


def _reject_constant(name):
    raise ACDError(f"{name} is not JSON")


def parse(data: bytes):
    """Step 1: UTF-8 without a byte order mark; JSON (RFC 8259) without duplicate member names; strings must be
    valid Unicode (no unpaired surrogates); numbers keep their lexical form."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ACDError("byte order mark")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ACDError(f"not UTF-8: {exc}") from None
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_float=Number, parse_int=Number,
                           parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise ACDError(f"not JSON: {exc}") from None
    _check_strings(value)
    return value


def _check_strings(v):
    if isinstance(v, dict):
        for k, x in v.items():
            _check_strings(k)
            _check_strings(x)
    elif isinstance(v, list):
        for x in v:
            _check_strings(x)
    elif isinstance(v, str) and not isinstance(v, Number):
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            raise ACDError("unpaired surrogate in a string") from None


def _references(v, out: set[str]) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if k == "reference" and isinstance(x, str) and not isinstance(x, Number):
                out.add(x)
            else:
                _references(x, out)
    elif isinstance(v, list):
        for x in v:
            _references(x, out)


def transform(bundle) -> dict:
    """Steps 2 to 4: remove the members and entries that attestation adds."""
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ACDError("not a Bundle")
    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict) or \
            not isinstance(entries[0].get("resource"), dict) or \
            entries[0]["resource"].get("resourceType") != "Composition":
        raise ACDError("the first entry is not a Composition")
    b = {k: v for k, v in bundle.items() if k not in ("id", "identifier", "timestamp")}          # step 2
    comp = dict(entries[0]["resource"])
    parties = set()
    for a in comp.get("attester") or []:
        ref = ((a or {}).get("party") or {}).get("reference") if isinstance(a, dict) else None
        if isinstance(ref, str):
            parties.add(ref)
    comp.pop("attester", None)                                                                   # step 3
    rest = [{**entries[0], "resource": comp}] + list(entries[1:])
    candidates = [e for e in rest[1:] if isinstance(e, dict) and e.get("fullUrl") in parties]   # step 4
    kept = [e for e in rest if not any(e is c for c in candidates)]
    refs: set[str] = set()
    _references({**b, "entry": kept}, refs)
    b["entry"] = [e for e in rest if not any(e is c for c in candidates) or e.get("fullUrl") in refs]
    return b


_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def serialise(v) -> str:
    """Step 5: RFC 8785 serialisation, except that numbers are written exactly as they appeared in the input."""
    if isinstance(v, Number):
        return str.__str__(v)
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        return "[" + ",".join(serialise(x) for x in v) + "]"
    if isinstance(v, dict):
        keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(_string(k) + ":" + serialise(v[k]) for k in keys) + "}"
    raise ACDError(f"unexpected value {v!r}")


def canonical(data: bytes) -> bytes:
    return serialise(transform(parse(data))).encode("utf-8")


def digest(data: bytes) -> str:
    """Step 6."""
    return hashlib.sha256(canonical(data)).hexdigest()


def main(argv: list[str]) -> int:
    canon = "--canonical" in argv
    path = [a for a in argv if not a.startswith("--")][0]
    try:
        data = open(path, "rb").read()
        if canon:
            sys.stdout.buffer.write(canonical(data))
        else:
            print(digest(data))
    except ACDError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
