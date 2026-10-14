"""Parity between the generated Playwright dictionary matcher and the runner's (BE-0463).

The JS helper reimplements the runner's text form of a JSON value, so it runs here under `node`
against the Python matcher on the same inputs. CI has `node` (the `lint-js` step uses it); a host
without it skips these tests.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bajutsu.codegen._playwright_dict_match import DICT_MATCH_JS
from bajutsu.codegen.playwright import to_playwright
from bajutsu.common.assertions.dict_match import json_text
from bajutsu.common.assertions.network import match_request
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario import RequestMatch, Scenario

_NODE = shutil.which("node")
needs_node = pytest.mark.skipif(_NODE is None, reason="node is not installed")

# The only TypeScript the helper uses (its module docstring keeps it to these forms).
_TS_ANNOTATION = re.compile(r"<string, any>|: (?:any|boolean|string|never)(?=[,)=;\s])")

_HARNESS = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const texts = input.bodies.map((b) => {
  try { return bajutsuText(bajutsuJson(b), false); } catch { return null; }
});
const results = input.cases.map((c) => bajutsuDict({ url: c.url, body: c.body }, c.query, c.match));
process.stdout.write(JSON.stringify({ texts, results }));
"""


def _run_node(tmp_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    assert _NODE is not None
    script = tmp_path / "parity.js"
    script.write_text(_TS_ANNOTATION.sub("", DICT_MATCH_JS) + _HARNESS, encoding="utf-8")
    out = subprocess.run(
        [_NODE, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    result: dict[str, Any] = json.loads(out.stdout)
    return result


def _py_text(body: str) -> str | None:
    try:
        return json_text(json.loads(body))
    except ValueError:
        return None


_BODIES = [
    "1", "-0", "1.0", "-0.0", "1e2", "1E-7", "0.0001", "0.00001", "3.14", "-2.5e-5", "1e16",
    "9999999999999999.0", "1.5e300", "1e999", "-1e999", "NaN", "-Infinity", "Infinity",
    "12345678901234567890", "123456789012345678901234567890.5", "true", "null", '"text"',
    '"é\\n\\u0001\\"\\\\/"',
    '{"b":1,"2":[1.0,true,null,"é\\n\\u0001"],"a":{"$x":-0.0}}',
    '{"dup":1,"k":2,"dup":3}',
    ' [ 1 , 2.50 , { } , [ ] ] ',
    # Not JSON for Python's reader either:
    "01", "[1,]", "{'a':1}", '{"a":1} x', "", "tru", '"\\x"', '"a\tb"',
]  # fmt: skip


@needs_node
def test_text_form_matches_the_runner(tmp_path: Path) -> None:
    rng = random.Random(463)
    floats = [json.dumps(rng.uniform(-1, 1) * 10 ** rng.randint(-30, 30)) for _ in range(300)]
    bodies = _BODIES + floats
    out = _run_node(tmp_path, {"bodies": bodies, "cases": []})
    assert out["texts"] == [_py_text(b) for b in bodies]


_ORDER = '{"user":{"id":42,"email":"a@b.c"},"items":[{"sku":"A-1","qty":1.0}],"2":"x"}'
_CASES: list[tuple[str, str | None, dict[str, Any] | None, dict[str, Any] | None]] = [
    ("https://a.test/o?page=2&sort=asc", None, {"page": "2"}, None),
    ("https://a.test/o?page=2&sort=asc", None, {"page": "3"}, None),
    ("https://a.test/o?q=a+b%21&debug&e=", None, {"q": "a b!", "debug": "", "e": ""}, None),
    ("https://a.test/o?tag=a&tag=b", None, {"tag": {"$matches": "^b$"}}, None),
    ("https://a.test/o?tag=a", None, {"x": {"$exists": False}, "tag": {"$exists": True}}, None),
    ("https://a.test/o?$ref=1", None, {"$$ref": "1"}, None),
    ("https://a.test/o", _ORDER, None, {"user": {"id": "42"}, "items": [{"qty": "1.0"}]}),
    ("https://a.test/o", _ORDER, None, {"items": [{"qty": "1"}]}),
    ("https://a.test/o", _ORDER, None, {"items": '[{"sku":"A-1","qty":1.0}]'}),
    ("https://a.test/o", _ORDER, None, {"user": '{"id":42,"email":"a@b.c"}', "2": "x"}),
    ("https://a.test/o", _ORDER, None, {"items": []}),
    ("https://a.test/o", _ORDER, None, {"user": {"email": {"$matches": "@b\\.c$"}}}),
    ("https://a.test/o", _ORDER, None, {"coupon": {"$exists": False}}),
    ("https://a.test/o", _ORDER, None, {"user": {"email": {"$exists": False}}}),
    ("https://a.test/o", _ORDER, None, {"user": "x"}),
    ("https://a.test/o", '{"$ref":"#"}', None, {"$$ref": "#"}),
    ("https://a.test/o", "[1]", None, {"x": {"$exists": False}}),
    ("https://a.test/o", "nope", None, {"x": {"$exists": False}}),
    ("https://a.test/o", None, None, {"x": {"$exists": False}}),
    ("https://a.test/o?v=1", '{"a":"1"}', {"v": "1"}, {"a": "1"}),
    ("https://a.test/o?v=2", '{"a":"1"}', {"v": "1"}, {"a": "1"}),
    ("https://a.test/o", "[" * 100_000, None, {"x": {"$exists": False}}),  # too deep: not JSON
]


@needs_node
def test_dictionary_verdicts_match_the_runner(tmp_path: Path) -> None:
    cases = [{"url": u, "body": b, "query": q, "match": m} for u, b, q, m in _CASES]
    out = _run_node(tmp_path, {"bodies": [], "cases": cases})
    expected = [
        match_request(
            NetworkExchange(url=u, requestBody=b),
            RequestMatch.model_validate({"query": q, "body": m}),
        )
        for u, b, q, m in _CASES
    ]
    assert out["results"] == expected
    assert True in expected and False in expected  # the table exercises both verdicts


def _scenario(expect: list[dict[str, Any]], steps: list[dict[str, Any]] | None = None) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": steps or [], "expect": expect})


def test_generated_test_carries_the_helper_only_when_used() -> None:
    plain = to_playwright([_scenario([{"request": {"path": "/a"}}])], "D", "http://x")
    assert "bajutsuDict" not in plain
    with_dicts = to_playwright(
        [
            _scenario(
                [{"request": {"path": "/a", "query": {"page": "2"}, "body": {"__proto__": "x"}}}]
            )
        ],
        "D",
        "http://x",
    )
    assert "const bajutsuDict = " in with_dicts
    assert (
        'bajutsuDict(e, JSON.parse(\'{"page":"2"}\'), JSON.parse(\'{"__proto__":"x"}\'))'
        in with_dicts
    )


def test_helper_is_emitted_for_sequences_and_until_waits() -> None:
    seq = _scenario([{"requestSequence": [{"path": "/a"}, {"path": "/b", "query": {"v": "1"}}]}])
    assert 'bajutsuDict(e, JSON.parse(\'{"v":"1"}\'), null)' in to_playwright(
        [seq], "D", "http://x"
    )
    wait = _scenario(
        [],
        steps=[{"wait": {"until": {"request": {"path": "/a", "body": {"k": "v"}}}, "timeout": 5}}],
    )
    assert "const bajutsuDict = " in to_playwright([wait], "D", "http://x")
