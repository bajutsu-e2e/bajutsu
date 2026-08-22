"""Redaction: masking secrets in evidence (text logs + element trees)."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink, intervals
from bajutsu.evidence.redaction import (
    CREDENTIAL_SHAPES,
    DEFAULT_CREDENTIAL_NAME_WORDS,
    PLACEHOLDER,
    Redactor,
    mask_credential_shapes,
    names_credential,
)
from bajutsu.evidence.sink import RunArtifactWriter
from bajutsu.scenario import Redact

# Two values with no recognizable credential shape, so the pattern backstop cannot reach them and
# only the rule under test can: whether they survive says exactly which default fired. The password
# is the one a remaining local run held verbatim, the reason the platform's own marking became a
# default; the key stands in for the `settings.apikey` value a `crawl --guide ai` invented.
INVENTED_VALUE = "invented-by-the-guide"
TYPED_PASSWORD = "Passw0rd!2026"


def _r(**kw: list[str]) -> Redactor:
    return Redactor(Redact(**kw))


def test_redact_text_masks_known_keys() -> None:
    red = _r(headers=["Authorization", "Cookie"], fields=["token", "password"])
    out = red.redact_text(
        "Authorization: Bearer abc.def\n"
        'POST {"token":"s3cret","keep":"ok"}\n'
        "url?password=hunter2&page=2\n"
        "nothing here\n"
    )
    assert "Bearer abc.def" not in out and "s3cret" not in out and "hunter2" not in out
    assert f"Authorization: {PLACEHOLDER}" in out
    assert f'"token":"{PLACEHOLDER}"' in out  # the quotes of a quoted value survive the mask
    assert f"password={PLACEHOLDER}" in out
    # untouched: non-secret content and keys
    assert '"keep":"ok"' in out
    assert "page=2" in out and "nothing here" in out


def test_redact_text_leaves_a_json_document_parseable() -> None:
    # The sink runs this pass over whole serialized documents, so a mask that drops a value's
    # quotes (`"token": [REDACTED]`) does not just look wrong — it makes the artifact unreadable to
    # every consumer that loads it, for any run configuring a key an artifact actually carries.
    document = json.dumps(
        {
            "kind": "waitTimeout",
            "value": "hunter2",
            "elements": [{"identifier": "auth.token", "value": "s3cret"}],
            "attempts": 3,
            "keep": "readable",
        },
        indent=2,
    )
    out = _r(fields=["value", "attempts"]).redact_text(document)
    loaded = json.loads(out)  # the assertion: still a document, not just still masked
    assert loaded["value"] == PLACEHOLDER
    assert loaded["elements"][0]["value"] == PLACEHOLDER
    # A bare value is quoted too: `"attempts": [REDACTED]` is not JSON, and a placeholder string is
    # valid wherever a value of any type stood.
    assert loaded["attempts"] == PLACEHOLDER
    assert "hunter2" not in out and "s3cret" not in out
    assert loaded["keep"] == "readable" and loaded["kind"] == "waitTimeout"


def test_redact_text_masks_percent_encoded_secret_value() -> None:
    # BE-0153: a secret carried as a URL query / form field is percent-encoded, so its
    # literal bytes never appear — the encoded form must be masked too.
    red = Redactor(Redact(), values=["s3cr@t/v!"])
    out = red.redact_text(
        "GET /login?token=s3cr%40t/v%21 HTTP/1.1\n"  # quote (default safe='/')
        "body: token=s3cr%40t%2Fv%21\n"  # quote_plus / safe=''
        "literal: s3cr@t/v!\n"
    )
    assert "s3cr%40t/v%21" not in out
    assert "s3cr%40t%2Fv%21" not in out
    assert "s3cr@t/v!" not in out
    assert out.count(PLACEHOLDER) == 3


def test_redact_text_masks_basic_auth_base64_secret_value() -> None:
    # BE-0153: HTTP Basic auth sends base64(user:pass); the literal password never appears
    # in the header text, only its base64-joined form — so a `Basic ...` token echoed into a
    # log or body (where header-name masking does not reach) must be decoded and masked.
    token = base64.b64encode(b"admin:hunter2").decode()
    red = Redactor(Redact(), values=["hunter2"])
    # The same secret appears both base64-encoded in the token and as a plain literal; the
    # Basic-auth decode and the literal-value pass must both fire without corrupting each other.
    out = red.redact_text(
        f"curl -H 'Authorization: Basic {token}' https://api.example.com\nlogged password: hunter2\n"
    )
    assert token not in out
    assert "hunter2" not in out
    assert f"Authorization: Basic {PLACEHOLDER}" in out
    # A Basic token that decodes to no known secret is left legible.
    other = base64.b64encode(b"guest:public").decode()
    assert other in red.redact_text(f"Authorization: Basic {other}\n")


def test_redact_text_masks_html_and_json_escaped_secret_value() -> None:
    # BE-0153: a secret embedded in an HTML attribute or a JSON string is escaped, so its
    # raw bytes never appear — the escaped forms must be masked too.
    value = 'a<b"c&d'
    red = Redactor(Redact(), values=[value])
    html_form = html.escape(value)  # a&lt;b&quot;c&amp;d
    json_form = json.dumps(value)[1:-1]  # a<b\"c&d
    out = red.redact_text(f'<input value=\'{html_form}\'>\n{{"note":"{json_form}"}}\n')
    assert html_form not in out
    assert json_form not in out
    assert value not in out
    assert out.count(PLACEHOLDER) == 2


def test_redact_exchange_masks_headers_url_and_body() -> None:
    red = _r(headers=["Authorization"], fields=["token", "password"])
    ex = red.redact_exchange(
        {
            "method": "POST",
            "url": "https://api.example.com/login?token=qstring",
            "requestHeaders": {"Authorization": "Bearer abc.def", "Accept": "application/json"},
            "requestBody": '{"name":"bajutsu","password":"hunter2"}',
            "responseBody": '{"token":"resp-secret"}',
        }
    )
    # Header masked whole by name; non-secret header untouched.
    assert ex["requestHeaders"]["Authorization"] == PLACEHOLDER
    assert ex["requestHeaders"]["Accept"] == "application/json"
    # Body fields and query params scrubbed (a whole-JSON text pass would miss escaped bodies).
    assert "hunter2" not in ex["requestBody"] and "resp-secret" not in ex["responseBody"]
    assert "qstring" not in ex["url"]
    assert "bajutsu" in ex["requestBody"]  # non-secret field kept
    # No-op when unconfigured.
    assert (
        Redactor(Redact()).redact_exchange({"requestBody": '{"password":"x"}'})["requestBody"]
        == '{"password":"x"}'
    )


def test_default_headers_masked_without_redact_block() -> None:
    # BE-0130: a scenario that never mentions `redact:` still masks the standard
    # credential-bearing headers, so plaintext tokens never land in network.json.
    ex = Redactor(Redact()).redact_exchange(
        {
            "requestHeaders": {"Authorization": "Bearer abc.def", "Accept": "application/json"},
            "responseHeaders": {"Set-Cookie": "session=s3cret", "Content-Type": "text/html"},
        }
    )
    assert ex["requestHeaders"]["Authorization"] == PLACEHOLDER
    assert ex["responseHeaders"]["Set-Cookie"] == PLACEHOLDER
    # Non-secret headers are still legible evidence.
    assert ex["requestHeaders"]["Accept"] == "application/json"
    assert ex["responseHeaders"]["Content-Type"] == "text/html"


def test_cookie_and_set_cookie_are_one_concern() -> None:
    # BE-0130: `cookie` and `set-cookie` carry the same secret in opposite directions;
    # naming either masks both.
    ex = _r(headers=["cookie"]).redact_exchange(
        {
            "requestHeaders": {"Cookie": "session=abc"},
            "responseHeaders": {"Set-Cookie": "session=abc; Path=/"},
        }
    )
    assert ex["requestHeaders"]["Cookie"] == PLACEHOLDER
    assert ex["responseHeaders"]["Set-Cookie"] == PLACEHOLDER


def test_unmask_headers_is_the_only_opt_out() -> None:
    # BE-0130: turning off a default is a visible, deliberate choice — not the mere
    # absence of `redact:`. Unmasking `cookie` releases `set-cookie` too (one concern).
    ex = Redactor(Redact(unmaskHeaders=["authorization", "cookie"])).redact_exchange(
        {
            "requestHeaders": {"Authorization": "Bearer raw", "Cookie": "session=raw"},
            "responseHeaders": {"Set-Cookie": "session=raw", "X-Api-Key": "still-secret"},
        }
    )
    assert ex["requestHeaders"]["Authorization"] == "Bearer raw"
    assert ex["requestHeaders"]["Cookie"] == "session=raw"
    assert ex["responseHeaders"]["Set-Cookie"] == "session=raw"
    # A default not named in unmaskHeaders stays masked.
    assert ex["responseHeaders"]["X-Api-Key"] == PLACEHOLDER


def test_redactor_inactive_when_unconfigured() -> None:
    red = Redactor(Redact())
    assert red.active is False
    assert red.redact_text("token=abc") == "token=abc"  # no-op
    assert red.has_label_rules is False


def test_has_label_rules_true_only_for_redact_labels() -> None:
    # A redactor active for other reasons (secret values, header/field keys) is not a label-rule
    # redactor — only `redact.labels` triggers the free-text-can't-honor-this gap.
    assert Redactor(Redact(labels=["Password"])).has_label_rules is True
    assert Redactor(Redact(), values=["s3kr3t"]).has_label_rules is False
    assert Redactor(Redact(fields=["token"])).has_label_rules is False


def _el(identifier: str, label: str, value: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": [],
        "frame": (0, 0, 1, 1),
        "nativeZ": None,
    }


def _secure_el(identifier: str, label: str, value: str) -> base.Element:
    """An element the platform itself marked a masked input."""
    element = _el(identifier, label, value)
    element["traits"] = [base.Trait.SECURE_TEXT_FIELD]
    return element


def test_redact_elements_masks_labeled_value() -> None:
    red = _r(labels=["Password"], fields=["token"])
    els = red.redact_elements(
        [
            _el("auth.password", "Password", "hunter2"),
            _el("note", "Note", "auth token=xyz here"),
            _el("plain", "Plain", "nothing secret"),
        ]
    )
    assert els[0]["value"] == PLACEHOLDER  # masked by label
    assert "xyz" not in (els[1]["value"] or "")  # embedded secret scrubbed
    assert els[2]["value"] == "nothing secret"  # untouched


def test_filesink_redacts_elements(tmp_path: Path) -> None:
    sink = FileSink(tmp_path / "run", redact=Redact(labels=["Password"]))
    driver = FakeDriver([_el("auth.password", "Password", "hunter2")])
    sink.capture(driver, "00-s/step0", ["elements"])
    data = json.loads(
        (tmp_path / "run" / "00-s" / "step0" / "elements.json").read_text(encoding="utf-8")
    )
    assert data[0]["value"] == PLACEHOLDER


def test_filesink_redacts_device_log_on_finish(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "00-s").mkdir(parents=True)
    log = run / "00-s" / "device.log"
    log.write_text("Authorization: Bearer abc\ntoken=secret\nnormal line\n", encoding="utf-8")
    sink = FileSink(run, redact=Redact(headers=["Authorization"], fields=["token"]))
    # A stopped interval whose artifact is this file (default _NullProc: stop() == path).
    sink.finish_scenario_intervals("00-s", [intervals.Interval(kind="deviceLog", path=log)])
    out = log.read_text(encoding="utf-8")
    assert "Bearer abc" not in out and "secret" not in out
    assert f"Authorization: {PLACEHOLDER}" in out and f"token={PLACEHOLDER}" in out
    assert "normal line" in out


def test_filesink_no_redact_leaves_files_untouched(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "00-s").mkdir(parents=True)
    log = run / "00-s" / "device.log"
    log.write_text("token=secret\n", encoding="utf-8")
    FileSink(run).finish_scenario_intervals(
        "00-s", [intervals.Interval(kind="deviceLog", path=log)]
    )
    assert log.read_text(encoding="utf-8") == "token=secret\n"  # no redact config -> unchanged


# --- BE-0331: the two element defaults that need no configuration ---------------------------


def test_platform_marked_field_is_masked_with_nothing_configured() -> None:
    # The case that matters: no `redact:` at all — what a `crawl` always has, since it carries no
    # scenario — so the redactor is otherwise inert and this default is the only thing standing
    # between the value and the artifact. The platform already stated the field's secrecy; a
    # configuration file should not have to restate it.
    red = Redactor(Redact())
    assert red.active is False
    (masked,) = red.redact_elements([_secure_el("field.7", "Enter it", TYPED_PASSWORD)])
    assert masked["value"] == PLACEHOLDER
    assert masked["label"] == "Enter it"  # a label is not a value — it stays legible evidence


def test_unmask_secure_fields_releases_the_platform_default() -> None:
    # Turning a default off is a visible, deliberate choice rather than the mere absence of
    # `redact:`, exactly as BE-0130's `unmaskHeaders` established for headers.
    (kept,) = Redactor(Redact(unmaskSecureFields=True)).redact_elements(
        [_secure_el("field.7", "Enter it", TYPED_PASSWORD)]
    )
    assert kept["value"] == TYPED_PASSWORD
    # Releasing one default must not release the other: a credential-named field is still masked.
    (still,) = Redactor(Redact(unmaskSecureFields=True)).redact_elements(
        [_el("settings.apikey", "Key", INVENTED_VALUE)]
    )
    assert still["value"] == PLACEHOLDER


def test_credential_named_field_is_masked_with_nothing_configured() -> None:
    # `settings.apikey` is the identifier the motivating leak carried: nothing was configured, the
    # value was invented by the guide rather than bound through `${secrets.X}`, and no rule reached
    # it. A label names a field just as well as an identifier does, so it is matched too.
    ident, labelled, plain = Redactor(Redact()).redact_elements(
        [
            _el("settings.apikey", "Key", INVENTED_VALUE),
            _el("field.7", "Access token", INVENTED_VALUE),
            _el("profile.nickname", "Nickname", "kitty"),
        ]
    )
    assert ident["value"] == PLACEHOLDER
    assert labelled["value"] == PLACEHOLDER
    assert plain["value"] == "kitty"  # nothing credential-like — ordinary evidence stays readable


def test_unmask_credential_names_releases_the_name_default() -> None:
    (kept,) = Redactor(Redact(unmaskCredentialNames=True)).redact_elements(
        [_el("settings.apikey", "Key", INVENTED_VALUE)]
    )
    assert kept["value"] == INVENTED_VALUE
    # As above, the two defaults release independently.
    (still,) = Redactor(Redact(unmaskCredentialNames=True)).redact_elements(
        [_secure_el("field.7", "Enter it", TYPED_PASSWORD)]
    )
    assert still["value"] == PLACEHOLDER


def test_credential_vocabulary_matches_on_word_boundaries() -> None:
    # A rule an author cannot predict is a rule an author cannot rely on, so both halves are pinned:
    # `apikey` and `api_key` are listed separately because `_` is a word character and one
    # word-boundary match never covers the other, while an unrelated `pinned` must not hit.
    assert names_credential("settings.apikey")
    assert names_credential("settings.api_key")
    assert names_credential(None, "Enter your PIN")
    assert not names_credential("prefs.pinned")
    assert not names_credential("profile.nickname", "Nickname")
    assert not names_credential(None, None)
    # Every documented word reaches the compiled pattern, so the vocabulary and the rule cannot
    # drift apart — the vocabulary is what the docs promise an author.
    for word in DEFAULT_CREDENTIAL_NAME_WORDS:
        assert names_credential(f"settings.{word}"), word


def test_a_default_masked_field_still_gets_its_label_scrubbed() -> None:
    # Blanking the value comes *after* the text scrub rather than instead of it: a credential-named
    # field often carries the secret in its label too, and skipping the scrub would mask the value
    # while handing the same secret over in the label beside it.
    red = Redactor(Redact(), values=["hunter2"])
    named, secure = red.redact_elements(
        [
            _el("settings.apikey", "Key (hunter2)", INVENTED_VALUE),
            _secure_el("field.7", "Hint: hunter2", TYPED_PASSWORD),
        ]
    )
    assert named["value"] == PLACEHOLDER and "hunter2" not in (named["label"] or "")
    assert secure["value"] == PLACEHOLDER and "hunter2" not in (secure["label"] or "")


# --- BE-0331: the screen map, whose actions are not elements --------------------------------


def _map_with(*actions: dict[str, Any]) -> dict[str, Any]:
    """A screen map shaped like `screenmap_dict`, with the same actions in all three places.

    `paths`, `crashes` and `pruned` are the only keys that carry a serialized action, and each nests
    it differently — so one fixture reused across the three is what shows the rule reaches all of
    them rather than the one the author happened to think of.
    """
    return {
        "nodes": [{"fingerprint": "abc", "ids": ["settings.apikey"]}],
        "edges": [{"src": "abc", "action": "type settings.apikey", "dst": "def"}],
        "paths": {"def": [dict(a) for a in actions]},
        "crashes": [{"path": ["type settings.apikey"], "actions": [dict(a) for a in actions]}],
        "pruned": [{"src": "abc", "key": "k", "owner": "abc", "path": [dict(a) for a in actions]}],
        "stop_reason": "completed",
    }


def _actions_of(screen_map: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """The three serialized action lists, so an assertion covers `paths`, `crashes` and `pruned`."""
    return [
        screen_map["paths"]["def"],
        screen_map["crashes"][0]["actions"],
        screen_map["pruned"][0]["path"],
    ]


def test_redact_screen_map_masks_a_credential_named_or_marked_target() -> None:
    # A screen-map action is not an `Element`, so `redact_elements` cannot see the pairing between
    # what an action targets and the value it entered, and the defaults are structural, so a
    # free-text pass over the serialized map reaches neither. This is the method that closes it.
    out = Redactor(Redact()).redact_screen_map(
        _map_with(
            {"kind": "type", "target": "settings.apikey", "value": INVENTED_VALUE},
            {"kind": "type", "target": "field.7", "value": TYPED_PASSWORD, "secure": True},
            {"kind": "type", "target": "search.query", "value": "kittens"},
        )
    )
    for actions in _actions_of(out):
        named, marked, ordinary = actions
        assert named["value"] == PLACEHOLDER
        assert marked["value"] == PLACEHOLDER  # `secure` stands in for the trait the map dropped
        assert ordinary["value"] == "kittens"  # what a crawl is written to explain, left readable


def test_redact_screen_map_masks_a_fill_by_action_and_by_field() -> None:
    # A fill records one value list, so its `secure` flag is the OR across its fields and masks the
    # whole list — over-masking a companion field is the only direction that cannot leak. A fill no
    # field marked still masks per field, since each pair names its own target.
    out = Redactor(Redact()).redact_screen_map(
        _map_with(
            {
                "kind": "fill",
                "fields": [["field.7", TYPED_PASSWORD], ["profile.nickname", "kitty"]],
                "secure": True,
            },
            {
                "kind": "fill",
                "fields": [["settings.apikey", INVENTED_VALUE], ["profile.nickname", "kitty"]],
            },
        )
    )
    for actions in _actions_of(out):
        marked, named = actions
        assert marked["fields"] == [["field.7", PLACEHOLDER], ["profile.nickname", PLACEHOLDER]]
        assert named["fields"] == [["settings.apikey", PLACEHOLDER], ["profile.nickname", "kitty"]]


def test_write_screen_map_scrubs_the_free_text_around_the_actions(tmp_path: Path) -> None:
    # The structural rule reaches an action's `value`/`fields` and nothing else, so a secret the app
    # echoes into an on-screen label rides out through a node's ids, an edge's description or a stop
    # reason — masked in elements.json and verbatim in screenmap.json (and the shared HTML report).
    writer = RunArtifactWriter(tmp_path / "runs" / "r1", Redactor(Redact(), values=["hunter2"]))
    path = writer.write_screen_map(
        "screenmap.json",
        {
            "nodes": [{"fingerprint": "abc", "ids": ["greeting.hunter2"], "label": "Hi, hunter2"}],
            "edges": [{"src": "abc", "action": "tap greeting.hunter2", "dst": "def"}],
            "crashes": [{"path": ["tap greeting.hunter2"], "actions": []}],
            "stop_reason": "crashed on hunter2's screen",
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "hunter2" not in text
    written = json.loads(text)  # the scrub must leave a document a resume can still read back
    assert written["nodes"][0]["ids"] == [f"greeting.{PLACEHOLDER}"]
    assert written["edges"][0]["src"] == "abc"


def test_sink_json_survives_a_key_pattern_that_would_eat_the_document(tmp_path: Path) -> None:
    # A key pattern reaches to end of line, so a *serialized* document is the one thing it must
    # never be shown: `token: ` consumes the closing quote and the delimiter after it, and it even
    # re-eats the redactor's own earlier output (`"token: [REDACTED]"`, left by `redact_elements`).
    # Both artifacts here are read back — `--continue` loads screenmap.json and the web UI polls it
    # live — so a document that stops parsing is a broken resume, not a cosmetic blemish.
    writer = RunArtifactWriter(tmp_path / "runs" / "r1", Redactor(Redact(fields=["token"])))
    elements: list[base.Element] = [
        {"identifier": "auth.token", "label": "token: abc123", "value": "s3cret"}
    ]
    wait_timeout = writer.write_json(
        "00-x/step0/wait-timeout.json",
        {
            "target": {"id": "auth.submit"},
            "token": 12345,  # a bare value: the mask must stay valid where a number stood
            "session": {"token": {"kind": "bearer"}},  # nor can a pattern consume an object
            "elements": writer.redactor.redact_elements(elements),
        },
    )
    screen_map = writer.write_screen_map(
        "screenmap.json",
        {"stop_reason": "completed", "nodes": [{"fingerprint": "abc", "ids": ["token: abc123"]}]},
    )
    for path in (wait_timeout, screen_map):
        text = path.read_text(encoding="utf-8")
        assert "abc123" not in text and "s3cret" not in text
        json.loads(text)  # the assertion: still a document every consumer can read back
    doc = json.loads(wait_timeout.read_text(encoding="utf-8"))
    assert doc["token"] == PLACEHOLDER and doc["session"]["token"] == PLACEHOLDER
    assert doc["elements"][0]["label"] == f"token: {PLACEHOLDER}"
    assert doc["target"] == {"id": "auth.submit"}  # nothing configured names it, so it is untouched


def test_write_screen_map_leaves_the_crawls_own_schema_keys_alone(tmp_path: Path) -> None:
    # `redact.fields` is a vocabulary of *app body* field names, so a target that happens to call one
    # `label`, `key` or `path` must not have the crawl's control data rewritten under it: the map is
    # read back by `--continue` / `--resume-key`, and a masked `path` is a bare string a resume would
    # walk character by character. Free text and configured *values* still have to be caught.
    writer = RunArtifactWriter(
        tmp_path / "runs" / "r1",
        Redactor(Redact(fields=["label", "key", "path"]), values=["hunter2"]),
    )
    path = writer.write_screen_map(
        "screenmap.json",
        {
            "nodes": [{"fingerprint": "abc", "ids": ["greeting"], "label": "Hi, hunter2"}],
            "edges": [{"src": "abc", "action": "tap greeting", "dst": "def"}],
            "pruned": [{"key": "abc:tap:greeting", "path": ["tap greeting"]}],
            "stop_reason": "completed",
        },
    )
    text = path.read_text(encoding="utf-8")
    assert "hunter2" not in text  # the known value is still masked, in a `label` the key rule skips
    written = json.loads(text)
    assert written["nodes"][0]["label"] == f"Hi, {PLACEHOLDER}"
    assert written["pruned"][0]["key"] == "abc:tap:greeting"  # `--resume-key` can still match it
    assert written["pruned"][0]["path"] == ["tap greeting"]  # still a list, not a masked string


def test_write_json_masks_a_keyed_value_nested_in_a_tuple(tmp_path: Path) -> None:
    # `json.dumps` serializes a tuple as an array without complaint, so a structure pass that walked
    # only lists would write the keyed value in plaintext and raise nothing — the silent class this
    # boundary exists to close. A document built from `dataclasses.asdict` keeps its tuples.
    writer = RunArtifactWriter(tmp_path / "runs" / "r1", Redactor(Redact(fields=["token"])))
    path = writer.write_json("d.json", {"path": ("step-a", {"token": "abc123"})})
    text = path.read_text(encoding="utf-8")
    assert "abc123" not in text
    assert json.loads(text)["path"] == ["step-a", {"token": PLACEHOLDER}]


def test_redact_screen_map_leaves_the_artifact_shape_unchanged() -> None:
    # The map is read back by a resume and by the web UI, so masking must rewrite values and nothing
    # else — a key invented here (a `secure` flag on an action that carried none) would change what
    # a reader deserializes.
    source = _map_with(
        {"kind": "type", "target": "settings.apikey", "value": INVENTED_VALUE},
        {"kind": "tap", "target": "home.submit"},
    )
    out = Redactor(Redact()).redact_screen_map(source)
    assert out.keys() == source.keys()
    assert out["nodes"] == source["nodes"] and out["edges"] == source["edges"]
    assert out["stop_reason"] == "completed"
    for actions, originals in zip(_actions_of(out), _actions_of(source), strict=True):
        assert [a.keys() for a in actions] == [o.keys() for o in originals]
    # A map with none of the three keys is returned intact rather than gaining them.
    assert Redactor(Redact()).redact_screen_map({"stop_reason": "max_steps"}) == {
        "stop_reason": "max_steps"
    }


# --- BE-0331: the pattern backstop ----------------------------------------------------------


def test_mask_credential_shapes_masks_every_documented_shape() -> None:
    # The backstop is the only rule that knows neither a configured name nor the value in advance,
    # so it is the one that can reach a value the tool itself generated. Each shape is exercised
    # with a value of that shape and nothing else, so a broken pattern names itself.
    samples = {
        "anthropicApiKey": "sk-ant-notarealkey000000000000",
        "awsAccessKeyId": "AKIAEXAMPLEKEYID1234",
        "githubToken": "ghp_notarealgithubtoken00000000000000000",
        "jsonWebToken": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJub25lIn0.c2lnbmF0dXJlLXBsYWNlaG9sZGVy",
        "pemPrivateKey": (
            "-----BEGIN RSA PRIVATE KEY-----\nbm90LWEta2V5\n-----END RSA PRIVATE KEY-----"
        ),
    }
    assert samples.keys() == {name for name, _ in CREDENTIAL_SHAPES}, (
        "a new shape needs a sample here, or the backstop ships untested"
    )
    for name, value in samples.items():
        masked, matched = mask_credential_shapes(f"the guide typed {value} into the field")
        assert matched == [name], value
        assert value not in masked
        assert PLACEHOLDER in masked


def test_mask_credential_shapes_leaves_ordinary_text_alone() -> None:
    # Nothing to report means nothing was masked, and the caller warns on the report — so a
    # false positive here would cry wolf on every artifact a crawl writes.
    text = "tap home.submit; type search.query; pinned=true; sk-ant is not a key"
    assert mask_credential_shapes(text) == (text, [])
