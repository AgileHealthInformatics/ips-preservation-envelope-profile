# Attested content digest (ACD-1): implementations and cross-language vectors

Algorithm ACD-1 is defined normatively in the profile, section *Attested content digest*. This folder holds:

| File | Role |
|---|---|
| `acd.py` | Reference implementation (Python 3, standard library only) |
| `acd.mjs` | Independent implementation (JavaScript, Node 18 or later) sharing no code with `acd.py` |
| `vectors/ACD-*.json` | Inputs, used byte for byte |
| `expected.json` | Expected digest, or `rejected`, for each input |

The profile build (`build.py`) runs both implementations over every vector and fails unless each result equals
`expected.json`, the canonical forms of the two implementations are identical, and the worked example in the profile
equals `vectors/ACD-01.json` and its digest. An implementation in another language is checked the same way: compute
the digest of each input and compare with `expected.json`.

```bash
python3 acd.py vectors/ACD-03.json          # digest
node acd.mjs vectors/ACD-03.json --canonical # canonical form
```
