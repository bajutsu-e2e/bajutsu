"""Redaction — mask secrets in captured evidence before it is written.

Driven by the resolved `Redact` config (header names, body/field names, and
accessibility labels). Free-text evidence (the device log, app trace) is scrubbed
by key→value patterns; the element tree is scrubbed structurally (an element's
value is masked when its label is configured, or when the value itself contains a
masked key). Images (screenshots/video) cannot be masked and are left as-is.
"""

from __future__ import annotations

import base64
import binascii
import html
import json
import re
import urllib.parse
from typing import Any

from bajutsu.common.drivers import base
from bajutsu.common.scenario import Redact

PLACEHOLDER = "[REDACTED]"


def _encoded_variants(value: str) -> set[str]:
    """Common encodings of a known secret value (BE-0153).

    A secret bound via `${secrets.X}` reaches evidence verbatim but often *encoded* — a
    URL query param is percent-encoded, so its literal bytes never appear. Masking these
    known encodings of a known value stays cheap and precise (we encode the value, then
    search for that), unlike decoding every string in evidence. The literal itself is not
    returned here; the caller already masks it.
    """
    variants = {
        urllib.parse.quote(value),  # default safe='/' — leaves path separators
        urllib.parse.quote(value, safe=""),  # encode everything, including '/'
        urllib.parse.quote_plus(value),  # form encoding — space becomes '+'
        html.escape(value),  # HTML attribute / text: & < > " '
        json.dumps(value)[1:-1],  # JSON string body, minus the surrounding quotes
    }
    return {v for v in variants if v != value}


# `Authorization: Basic <base64(user:pass)>` — the token is standard-base64 (no urlsafe).
_BASIC_AUTH = re.compile(r"(?i)(Basic\s+)([A-Za-z0-9+/]+={0,2})")

# BE-0130: credential-bearing headers masked by name even when a scenario omits `redact:`,
# so a shared/AI-bound network.json never hands over a live token by default. An author who
# genuinely needs a raw value opts out visibly via `redact.unmaskHeaders`.
DEFAULT_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
    }
)

# `cookie` (client→server) and `set-cookie` (server→client) carry the same secret in
# opposite directions, so naming — or unmasking — either covers both.
_COOKIE_HEADERS = frozenset({"cookie", "set-cookie"})

# BE-0331: an element whose identifier or label names a credential has its value masked with no
# configuration, the second of the two defaults whose secrecy is knowable at capture time. Kept
# small and documented rather than clever — a rule an author cannot predict is one they cannot rely
# on. Both `apikey` and `api_key` are listed because `_` is a word character, so one word-boundary
# match never covers the other.
DEFAULT_CREDENTIAL_NAME_WORDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
        "api_key",
        "credential",
        "otp",
        "pin",
    }
)

# Matched case-insensitively on word boundaries, so `settings.apikey` hits (the leak that motivated
# BE-0331) while an unrelated `pinned` does not.
_CREDENTIAL_NAME = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(w) for w in sorted(DEFAULT_CREDENTIAL_NAME_WORDS)) + r")\b"
)

# BE-0331's pattern backstop: high-confidence credential *shapes*, masked wherever they appear in
# text an artifact is about to carry. This is the rule that needs to know neither a configured name
# nor the value in advance, so it is the only one that can reach a value the tool itself generated
# (an AI guide inventing a realistic API key) or one an uploading worker's host holds no secrets for.
# Literal regular expressions only — no model is consulted, so prime directive 1 is untouched.
CREDENTIAL_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropicApiKey", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("awsAccessKeyId", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("githubToken", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    # The `eyJ` prefix is base64 of a JSON object's opening, so requiring it keeps this from
    # matching any three dot-separated tokens.
    ("jsonWebToken", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # Mask the whole block when its END line is present, so the key body goes with the header; an
    # unterminated header still masks on its own rather than being passed over.
    (
        "pemPrivateKey",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----)?",
            re.S,
        ),
    ),
)


def mask_credential_shapes(text: str) -> tuple[str, list[str]]:
    """Mask recognizable credential shapes in text, and name the shapes that matched.

    The caller warns on a non-empty second element: a value reaching the backstop means an earlier,
    more precise rule should have caught it, so the match is worth surfacing rather than masking
    silently.
    """
    matched: list[str] = []
    for name, pattern in CREDENTIAL_SHAPES:
        text, count = pattern.subn(PLACEHOLDER, text)
        if count:
            matched.append(name)
    return text, matched


def _as_str(value: Any) -> str | None:
    """A JSON field as text, or None when it is absent or another type."""
    return value if isinstance(value, str) else None


def names_credential(*texts: str | None) -> bool:
    """Whether any of an element's names (its identifier, its label) names a credential."""
    return any(_CREDENTIAL_NAME.search(t) for t in texts if t)


def _with_cookie_linkage(names: set[str]) -> set[str]:
    return names | _COOKIE_HEADERS if names & _COOKIE_HEADERS else names


def _masked(m: re.Match[str]) -> str:
    """The key, then the placeholder — quoted whenever the pattern consumed a JSON value.

    Only the JSON pattern captures the value (group 2), hence the arity check, and its replacement
    has to stay valid *in place*: a quoted string is legal JSON wherever a value of any type stood,
    while the bare `"token": [REDACTED]` a plain placeholder leaves behind is not — so a number or a
    literal is quoted too, not just a string.
    """
    if m.re.groups > 1:
        return f'{m.group(1)}"{PLACEHOLDER}"'
    return m.group(1) + PLACEHOLDER


def _patterns(keys: list[str]) -> list[re.Pattern[str]]:
    """For each key, patterns that capture the key (group 1) and consume its value."""
    pats: list[re.Pattern[str]] = []
    for key in keys:
        k = re.escape(key)
        # JSON: "key": "value"  or  "key": value — group 2 is the consumed value, so `_masked` can
        # tell a quoted value (whose quotes must survive) from a bare one.
        pats.append(re.compile(rf'("{k}"\s*:\s*)("(?:[^"\\]|\\.)*"|[^\s,}}\]]+)', re.I))
        # query / key=value
        pats.append(re.compile(rf"(?i)\b({k}\s*=\s*)[^\s&;,\"]+"))
        # header-ish: key: value-to-end-of-line
        pats.append(re.compile(rf"(?i)\b({k}\s*:\s*).+"))
    return pats


class Redactor:
    """Applies a `Redact` config to evidence. A no-op when nothing is configured.

    `values` are literal secret values (resolved from the environment) masked wherever
    they appear — this catches a secret the app echoes into a log / element / response,
    which key-based patterns alone would miss. Longest values are masked first so a value
    that is a substring of another does not leave a partial leak.
    """

    def __init__(self, redact: Redact | None, values: list[str] | None = None) -> None:
        redact = redact or Redact()
        self._keys: list[str] = [*redact.headers, *redact.fields]
        # The same configured names, matched against a *structure's* keys rather than serialized
        # text, so `redact_structure` can mask a value the key patterns must never be shown.
        self._key_names: set[str] = {k.lower() for k in self._keys}
        masked = set(DEFAULT_SENSITIVE_HEADERS) | _with_cookie_linkage(
            {h.lower() for h in redact.headers}
        )
        unmasked = _with_cookie_linkage({h.lower() for h in redact.unmask_headers})
        self._header_names: set[str] = masked - unmasked
        self._labels: set[str] = set(redact.labels)
        # BE-0331's two element defaults run unless explicitly released, so they hold on a run that
        # configures no `redact:` at all — a `crawl`, which has no scenario to carry one.
        self._mask_secure_fields = not redact.unmask_secure_fields
        self._mask_credential_names = not redact.unmask_credential_names
        self._patterns = _patterns(self._keys)
        self._raw_values: list[str] = [v for v in (values or []) if v]
        raw = set(self._raw_values)
        # Search terms are each raw value plus its common encodings (BE-0153). Longest
        # first so a term that is a substring of another never leaves a partial leak.
        terms = raw | {enc for v in raw for enc in _encoded_variants(v)}
        self._values: list[str] = sorted(terms, key=len, reverse=True)

    @property
    def active(self) -> bool:
        return bool(self._keys or self._labels or self._values)

    @property
    def has_label_rules(self) -> bool:
        """Whether `redact.labels` configures any structural, by-label masking.

        `redact_elements` can honor a label rule (it has the parsed tree, so it knows which
        element's `value` to blank); `redact_text` cannot — free text carries no element/label
        structure to match against, only key patterns and literal secret values. A caller writing
        an artifact `redact_elements` would mask (but this redactor can only run `redact_text`
        over) needs to know that gap exists, rather than silently writing an unmasked superset.
        """
        return bool(self._labels)

    def redact_text(self, text: str) -> str:
        """Mask secrets in free text (logs/traces).

        Masks the value after any configured key, and any literal secret value.
        """
        for pattern in self._patterns:
            text = pattern.sub(_masked, text)
        return self.redact_values(text)

    def redact_values(self, text: str) -> str:
        """Mask only the literal secret values (and a Basic-auth token carrying one) in text.

        The key-free half of `redact_text`, split out because it is the only half safe to run over a
        *serialized* document: a key pattern reaches to end of line, so inside a JSON string it eats
        the closing quote and the artifact stops parsing. The sink pairs this with
        `redact_structure`, which applies the key rules where the structure still exists.
        """
        # Decode Basic-auth tokens before the literal-value pass: a secret whose bytes happen
        # to fall inside a base64 token would otherwise splice PLACEHOLDER into it, breaking the
        # decode and leaking the token's tail. Masking the whole token first avoids that.
        if self._raw_values:
            text = self._redact_basic_auth(text)
        for value in self._values:
            text = text.replace(value, PLACEHOLDER)
        return text

    def redact_structure(self, data: Any, *, keys: bool = True) -> Any:
        """Mask secrets in a JSON-shaped structure, before anything serializes it.

        Keys are matched against the structure's own names and text rules against its string leaves,
        so a document whose shape carries no dedicated rule (`wait-timeout.json`, a manifest) is
        covered without a key pattern ever meeting a delimiter it would consume. Masking a keyed
        value with the placeholder *string* keeps the document valid whatever type it replaced.

        `keys=False` keeps the string-leaf half and drops the key match, for a document whose names
        are Bajutsu's own control schema rather than an app's data. `redact.fields` is a vocabulary
        of app body field names, so a target that happens to call a body field `label`, `key` or
        `path` would otherwise rewrite that control data — and the caller reading the artifact back
        resolves nothing, or walks a masked string character by character (BE-0331).
        """
        if not self.active:
            return data
        if isinstance(data, dict):
            return {
                k: PLACEHOLDER
                if keys and isinstance(k, str) and k.lower() in self._key_names
                else self.redact_structure(v, keys=keys)
                for k, v in data.items()
            }
        # A tuple serializes as an array just like a list, so skipping it here would let a keyed
        # value inside one reach the artifact unmasked with nothing raised — and a document built
        # from `dataclasses.asdict` keeps its tuples. The masked copy is a list; the next stop is
        # `json.dumps`, which cannot tell the two apart.
        if isinstance(data, list | tuple):
            return [self.redact_structure(v, keys=keys) for v in data]
        if isinstance(data, str):
            return self.redact_text(data)
        return data

    def _redact_basic_auth(self, text: str) -> str:
        """Mask a `Basic <base64>` token whose decoded `user:pass` carries a known secret.

        Forward-encoding cannot reach this: the username is unknown, so the base64 form is
        not derivable from the password alone. Decoding is scoped to the bounded token that
        follows `Basic ` — not a decode-everything scan — so it stays cheap and precise.
        """

        def mask(m: re.Match[str]) -> str:
            try:
                decoded = base64.b64decode(m.group(2), validate=True).decode("utf-8", "replace")
            except (binascii.Error, ValueError):
                return m.group(0)  # not valid base64 — leave it
            if any(v in decoded for v in self._raw_values):
                return m.group(1) + PLACEHOLDER
            return m.group(0)

        return _BASIC_AUTH.sub(mask, text)

    def masks_by_default(
        self, *, identifier: str | None, label: str | None, traits: list[str] | None = None
    ) -> bool:
        """Whether a BE-0331 default masks this field's value with no configuration.

        Either the platform marked the field secret, or its identifier or label names a credential.
        Public because the same two rules govern a value an artifact records *about* a field — a
        crawl action's typed text — where no `Element` survives to apply them structurally.
        """
        if self._mask_secure_fields and base.Trait.SECURE_TEXT_FIELD in (traits or []):
            return True
        return self._mask_credential_names and names_credential(identifier, label)

    def redact_elements(self, elements: list[base.Element]) -> list[base.Element]:
        """Mask secrets in an element tree.

        Mask an element's value fully when a default applies (BE-0331) or its label is configured;
        otherwise scrub the label/value text in case a secret is embedded there.
        """
        # The two defaults run regardless of `active`, the way `redact_exchange` already runs the
        # default header set: protection that arrives only when someone remembers to ask for it is
        # absent on the runs that need it most.
        if not (self.active or self._mask_secure_fields or self._mask_credential_names):
            return elements
        out: list[base.Element] = []
        for el in elements:
            new = dict(el)
            if new.get("label") in self._labels:
                new["value"] = PLACEHOLDER
            else:
                for field in ("value", "label"):
                    raw = new.get(field)
                    if isinstance(raw, str) and raw:
                        new[field] = self.redact_text(raw)
                # Blanking the value comes *after* the text scrub rather than instead of it: a
                # credential-named field often carries the secret in its label too, and skipping the
                # scrub would mask the value while leaving that label verbatim.
                if self.masks_by_default(
                    identifier=el.get("identifier"),
                    label=el.get("label"),
                    traits=el.get("traits"),
                ):
                    new["value"] = PLACEHOLDER
            out.append(new)  # type: ignore[arg-type]
        return out

    def redact_screen_map(self, screen_map: dict[str, Any]) -> dict[str, Any]:
        """Mask the input values a crawl's screen map records (BE-0331).

        A screen-map action is not an `Element`, so `redact_elements` cannot see the pairing between
        what an action targets and the value it entered; it is not a network exchange either, and the
        two defaults are structural, so a free-text pass over the serialized map reaches neither.
        This keys the same defaults on each action's own target and on the masked-input flag the
        crawl recorded beside it, and masks that action's value.

        Only the three places `screenmap_dict` puts a serialized action are rewritten, and a key the
        map does not carry is not added, so the artifact's shape is unchanged.
        """
        out = dict(screen_map)
        if isinstance(crashes := out.get("crashes"), list):
            out["crashes"] = [self._redact_action_list(c, "actions") for c in crashes]
        if isinstance(paths := out.get("paths"), dict):
            out["paths"] = {fp: self._redact_actions(acts) for fp, acts in paths.items()}
        if isinstance(pruned := out.get("pruned"), list):
            out["pruned"] = [self._redact_action_list(p, "path") for p in pruned]
        return out

    def _redact_action_list(self, holder: Any, key: str) -> Any:
        if not isinstance(holder, dict):
            return holder
        return {**holder, key: self._redact_actions(holder.get(key))}

    def _redact_actions(self, actions: Any) -> Any:
        if not isinstance(actions, list):
            return actions
        return [self._redact_action(a) for a in actions]

    def _redact_action(self, action: Any) -> Any:
        if not isinstance(action, dict):
            return action
        out = dict(action)
        # `secure` is the crawl's record of the platform's own marking, so it stands in for the
        # trait the map no longer carries an element to read.
        masked = self.masks_by_default(
            identifier=_as_str(out.get("target")),
            label=_as_str(out.get("label")),
            traits=[base.Trait.SECURE_TEXT_FIELD] if out.get("secure") else [],
        )
        if isinstance(value := out.get("value"), str):
            out["value"] = PLACEHOLDER if masked else self.redact_text(value)
        if isinstance(fields := out.get("fields"), list):
            out["fields"] = [self._redact_field(f, masked) for f in fields]
        return out

    def _redact_field(self, field: Any, action_masked: bool) -> Any:
        """Mask one `[id, value]` pair of a fill action.

        A fill's `secure` flag is the OR across its fields, so one masked input masks the whole
        action's values. Over-masking a companion field is the safe direction: the map records the
        values as one list, and under-masking would leak.
        """
        if not (isinstance(field, list) and len(field) == 2 and isinstance(field[1], str)):
            return field
        fid = _as_str(field[0])
        masked = action_masked or self.masks_by_default(identifier=fid, label=None)
        return [field[0], PLACEHOLDER if masked else self.redact_text(field[1])]

    def redact_exchange(self, exchange: dict[str, Any]) -> dict[str, Any]:
        """Mask secrets in one network-exchange dict.

        A header value is masked whole when its name is a sensitive header — the built-in
        default set plus any the scenario named (BE-0130), so header masking runs even when
        the redactor is otherwise inactive — else scrubbed as free text, and the url / bodies
        are scrubbed as free text so query params and body fields (token / password) are
        caught — which a whole-JSON text pass misses, since bodies are escaped strings.
        """
        # Default sensitive headers mask regardless of `active`, so this is only a no-op
        # when the built-in set has been fully unmasked and nothing else is configured.
        if not self.active and not self._header_names:
            return exchange
        out = dict(exchange)
        for key in ("requestHeaders", "responseHeaders"):
            headers = out.get(key)
            if isinstance(headers, dict):
                out[key] = {
                    k: PLACEHOLDER
                    if str(k).lower() in self._header_names
                    else self.redact_text(str(v))
                    for k, v in headers.items()
                }
        for key in ("url", "requestBody", "responseBody"):
            value = out.get(key)
            if isinstance(value, str):
                out[key] = self.redact_text(value)
        return out
