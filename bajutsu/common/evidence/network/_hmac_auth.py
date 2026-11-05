"""The signed scheme the app and the collector authenticate each other with (BE-0459).

One definition of both canonical forms, shared by the collector and its tests; BajutsuKit's
`CollectorSigning.swift` implements the same bytes, and the fixed vectors in
`tests/fixtures/be0459/collector_hmac_vectors.json` pin the two sides together.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# The request header's scheme name, and the answer header that carries the collector's signature.
SCHEME = "Bajutsu-HMAC-SHA256"
ANSWER_HEADER = "X-Bajutsu-Signature"

# The version lines open each canonical form, so a later scheme can change the form without either
# side misreading the other's bytes.
_REQUEST_VERSION = "bajutsu-request-v1"
_ANSWER_VERSION = "bajutsu-answer-v1"

_NONCE_BYTES = 16


def b64url(data: bytes) -> str:
    """Unpadded base64url, the encoding of every nonce and signature in the scheme."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_nonce() -> str:
    """A fresh 16-byte nonce, encoded for the `Authorization` header."""
    return b64url(secrets.token_bytes(_NONCE_BYTES))


def canonical_path(target: str) -> str:
    """The path a signature covers: the request target without its query, `/` when empty.

    Kept percent-encoded as the request line carries it, so both sides sign the same bytes without
    agreeing on a decoding.
    """
    path = target.split("?", 1)[0].split("#", 1)[0]
    return path or "/"


def request_canonical(method: str, path: str, nonce: str, body_digest: str) -> bytes:
    """The bytes a request signature covers; `body_digest` is the body's lowercase hex SHA-256."""
    return "\n".join((_REQUEST_VERSION, method, path, nonce, body_digest)).encode()


def answer_canonical(nonce: str, status: int, body_digest: str) -> bytes:
    """The bytes an answer signature covers, bound to the request by that request's nonce."""
    return "\n".join((_ANSWER_VERSION, nonce, str(status), body_digest)).encode()


def _mac(token: str, canonical: bytes) -> str:
    return b64url(hmac.new(token.encode(), canonical, hashlib.sha256).digest())


def sign_request(token: str, method: str, path: str, nonce: str, body_digest: str) -> str:
    """The request signature for an already-hashed body."""
    return _mac(token, request_canonical(method, path, nonce, body_digest))


def sign_answer(token: str, nonce: str, status: int, body: bytes) -> str:
    """The `X-Bajutsu-Signature` value for one answer to the request that carried `nonce`."""
    return _mac(token, answer_canonical(nonce, status, hashlib.sha256(body).hexdigest()))


def authorization(token: str, method: str, target: str, body: bytes, nonce: str) -> str:
    """The `Authorization` value a client sends — what BajutsuKit builds, for tests and tooling."""
    signature = sign_request(
        token, method, canonical_path(target), nonce, hashlib.sha256(body).hexdigest()
    )
    return f"{SCHEME} nonce={nonce}, signature={signature}"


def parse_authorization(value: str) -> tuple[str, str] | None:
    """The `(nonce, signature)` a signed `Authorization` value carries, or None when malformed.

    Strict on purpose: a header that names the scheme but omits, repeats, or adds a parameter is
    refused rather than guessed at.
    """
    scheme, _, rest = value.partition(" ")
    if scheme != SCHEME:
        return None
    params: dict[str, str] = {}
    for part in rest.split(","):
        key, sep, val = part.strip().partition("=")
        if not sep or not val or key in params:
            return None
        params[key] = val
    if params.keys() != {"nonce", "signature"}:
        return None
    return params["nonce"], params["signature"]


def signatures_match(expected: str, presented: str) -> bool:
    """Constant-time comparison of two encoded signatures."""
    return hmac.compare_digest(expected.encode(), presented.encode())
