# Collector signing vectors (BE-0459)

`collector_hmac_vectors.json` pins the signed scheme between BajutsuKit and the host's network
collector byte for byte, as
[BE-0459](../../../roadmaps/BE-0459-real-device-collector-hmac-auth/BE-0459-real-device-collector-hmac-auth.md)
specifies it. Two suites read the same file:

- `tests/test_collector_hmac.py` checks the Python reference
  (`bajutsu/common/evidence/network/_hmac_auth.py`).
- `CollectorSigningTests.swift` in `BajutsuKit/Tests/BajutsuKitTests/` checks the Swift side.

A drift in either implementation then fails its own suite rather than a real-device run.

- `requests`: token, method, canonical path, nonce, body, and the expected request signature. One
  vector is a report POST to the collector's bare URL, whose path is `/`.
- `answers`: token, the request's nonce, status, body, and the expected answer signature.
- `paths`: a request target and the path a signature covers (query dropped, `/` when empty).

Regenerate the signatures only with a deliberate change of scheme, together with its version line.
