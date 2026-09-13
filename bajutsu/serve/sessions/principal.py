"""Who a live session belongs to — the read the request gate branches on."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

#: A session belongs either to a person who signed in, or to a CI pipeline that exchanged an OIDC
#: token for one (BE-0414). The two are governed by different gates — a human by the viewer /
#: editor / admin role, a machine by an endpoint allowlist — so the store records which it is
#: rather than letting the gate infer it from the shape of an identity string.
PrincipalKind = Literal["human", "machine"]

HUMAN: PrincipalKind = "human"
MACHINE: PrincipalKind = "machine"

#: The reserved prefix a machine session's identity carries. A GitHub login cannot contain `/`, and
#: the repository that follows always does, so the form can never collide with a person's.
_MACHINE_PREFIX = "repo:"

_logger = logging.getLogger(__name__)


def machine_identity(repository: str) -> str:
    """The session identity a pipeline acting for *repository* is minted with (BE-0414).

    Minting and reading the form live together so they cannot drift apart — the identity has to be
    non-None at all for the session to be revocable, since `revoke_identities` works by identity and
    never touches a session carrying none.

    Case-folded, because `AllowedRepository.admits` matches the roster case-insensitively: GitHub
    will not let `Acme/App` and `acme/app` both exist, so an entry written in either casing admits
    the same repository. Revocation compares identities by exact equality, so minting from the raw
    claim would leave an admin who types the casing their own roster uses revoking nothing — and a
    revocation that matches nothing looks identical to one that had nothing left to match.
    """
    return f"{_MACHINE_PREFIX}{repository.lower()}"


def machine_repository(identity: str | None) -> str | None:
    """The repository behind a machine *identity*, or None for a human or token caller.

    Reading the identity's shape is right *here* and wrong in the request gate. Which gate governs a
    session is a security decision, and it reads the kind the store recorded
    (`Principal.kind`) — never a string convention a future identity format could break. This answers
    a different question: having already established what the caller is, how should the audit trail
    name it? A mis-read there writes a slightly wrong log line rather than admitting anyone.
    """
    if identity is None or not identity.startswith(_MACHINE_PREFIX):
        return None
    # The prefix alone settles that the caller is a machine. Returning None for a bare `repo:` would
    # answer "not a machine" for one that is, and `_record_audit` reads that answer as "write the
    # identity into `actor_id`" — a foreign key no pipeline has a row behind.
    return identity[len(_MACHINE_PREFIX) :]


@dataclass(frozen=True)
class Principal:
    """Who a session belongs to, as the gate reads it.

    Attributes:
        identity: The GitHub login for a human session, `repo:<owner>/<repo>` for a machine one, or
            None for a shared-token login that carries no identity at all. A login cannot contain
            `/`, so the machine form can never collide with one — and a non-None identity is what
            makes a session revocable, since `revoke_identities` never touches one carrying none.
        org: The tenant a machine session acts as, resolved once at the exchange and carried here
            rather than re-derived per request. None for a human session, whose org comes from
            their persisted user row instead.
        kind: Which gate governs this session.
    """

    identity: str | None
    org: str | None = None
    kind: PrincipalKind = HUMAN

    @classmethod
    def from_stored(cls, identity: object, org: object, kind: object) -> Principal:
        """Narrow a principal read back out of a store, whose fields arrive untyped.

        One place for the whole read-side narrowing, rather than one check per store: a session
        row reaches this from a JSON blob (Redis) or from bare `String` columns with no CHECK
        constraint behind them (SQL), so nothing upstream of here proves any of the three fields
        has the type the dataclass declares.

        A value of any other type reads as absent rather than being coerced — the same rule
        `oidc._text` applies to a claim, and for the same reason: a coerced value can match
        something, where an absent one cannot.
        """
        return cls(
            identity=identity if isinstance(identity, str) and identity else None,
            org=org if isinstance(org, str) and org else None,
            kind=kind_from_stored(kind),
        )


def kind_from_stored(value: object) -> PrincipalKind:
    """Narrow a kind read back out of a store to the two this code knows.

    Only an absent value and the literal `"human"` read as human: an absent one is a session row
    written before these columns existed, which predates machine sessions entirely. Anything else
    — a value from a newer version, a corrupted one — reads as *machine*, the kind the gate governs
    more narrowly, so an unrecognized session is refused rather than handed a human's role gate.

    "More narrowly" holds because `gate.forbidden_for_machine` refuses a machine principal outright
    when it carries no org, and an unrecognized row reaches that branch with `org=None` — only the
    exchange ever sets one. A revocation matches the same way, by "not human", so a row a newer
    version wrote is both governed and revoked as the machine the gate already treats it as.
    """
    if value is None or value == HUMAN:
        return HUMAN
    if value != MACHINE:
        # Only a session that *was* human can carry an unrecognized kind — the exchange always
        # writes "machine" — so this refuses one wholesale. Leave a trace, or the operator sees a
        # person 403'd on every endpoint with nothing anywhere explaining why.
        _logger.warning(
            "session kind %r is not one this version knows; refusing it as a machine", value
        )
    return MACHINE
