"""Redaction: masking secrets in evidence (text logs + element trees)."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import FileSink, intervals
from bajutsu.evidence.redaction import PLACEHOLDER, Redactor
from bajutsu.scenario import Redact


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
    assert f'"token":{PLACEHOLDER}' in out
    assert f"password={PLACEHOLDER}" in out
    # untouched: non-secret content and keys
    assert '"keep":"ok"' in out
    assert "page=2" in out and "nothing here" in out


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


def _el(identifier: str, label: str, value: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "value": value,
        "traits": [],
        "frame": (0, 0, 1, 1),
    }


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
