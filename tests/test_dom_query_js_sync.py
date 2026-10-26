"""Pin the WebView bridge's hand-kept copy of `QUERY_JS` to the Python original.

`BajutsuWebView.queryJS` cannot import the Python string, so without this check the two drift
silently and the hybrid-WebView bridge maps the same DOM to a different `Element` than the
Playwright backend.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from bajutsu.common.drivers.dom import QUERY_JS

_SWIFT = Path(__file__).resolve().parents[1] / "BajutsuKit/Sources/BajutsuKit/BajutsuWebView.swift"


def _swift_query_js() -> str:
    match = re.search(r'static let queryJS = """\n(.*?)\n\s*"""', _SWIFT.read_text(), re.S)
    assert match, "queryJS literal not found in BajutsuWebView.swift"
    return textwrap.dedent(match.group(1))


def test_swift_query_js_matches_python_query_js() -> None:
    swift = _swift_query_js()
    # Swift evaluates the script as an expression, so it wraps the same arrow function in an IIFE.
    assert swift.startswith("(() => {\n") and swift.endswith("\n})()")
    assert swift[1:-3] == QUERY_JS.strip()
