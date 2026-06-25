"""Tests for the HTML report network tab and exchange interleaving."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _report import _passing

from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult, StepOutcome
from bajutsu.report import html_report


def test_network_json_is_read_once_per_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The result timeline and the Network tab both need the exchanges; they must share one read,
    # not parse the (potentially large, body-carrying) network.json twice per scenario.
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://example.com/x","status":200,"startedAt":0.1}]',
        encoding="utf-8",
    )
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    from bajutsu.report import panels

    reads: list[str] = []
    real = panels._read_json

    def counting(run_dir: Path, name: str) -> Any:
        reads.append(name)
        return real(run_dir, name)

    monkeypatch.setattr(panels, "_read_json", counting)
    html_report("run1", [r], tmp_path)
    assert reads.count(f"{sid}/network.json") == 1


def test_html_network_tab(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://example.com/items","path":"/items","status":200,'
        '"durationMs":75.4,"startedAt":0.8,"responseHeaders":{"Content-Type":"text/html"},'
        '"responseBody":"<html>hi</html>"}]',
        encoding="utf-8",
    )
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path)
    # A Network tab appears and renders the captured exchange: request time / method /
    # path / status, plus the headers and (HTML-escaped) body when expanded.
    assert 'data-tab="net"' in out
    assert "captured by BajutsuKit" in out
    assert 'class="nxat muted"' in out and ">0.8s</span>" in out  # the request time on the row
    assert 'class="nxm">GET' in out
    assert "/items" in out and 'nxs ok">200' in out
    assert "Content-Type" in out and "&lt;html&gt;hi&lt;/html&gt;" in out
    # No network artifact -> no Network tab.
    assert 'data-tab="net"' not in html_report("run1", [_passing()])


def test_html_exchanges_interleaved_into_steps(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://api.example.com/items","status":200,"durationMs":50,'
        '"responseHeaders":{"Content-Type":"application/json"},"responseBody":"hello-body","startedAt":0.5},'
        '{"method":"POST","url":"https://other.com/log","status":204,"startedAt":1.2}]',
        encoding="utf-8",
    )
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}, {"wait": {"until": "settled", "timeout": 3}}],
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, started_at=1.0),
        ],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path, definitions=[definition])
    # Each exchange splits into a request row (method badge) and a response row.
    assert 'class="act act-net">GET' in out and 'act-net">response' in out
    # The row is a click target; its settings expand into a full-width row below.
    assert 'class="xmark">▸' in out and "class='nxdetail' hidden" in out
    assert 'class="nxk">endpoint' in out and "https://api.example.com/items" in out
    # The response row carries the response headers and (viewable) body.
    assert 'class="nxk">headers' in out and "Content-Type" in out
    assert 'class="nxk">body' in out and "hello-body" in out
    # Time order: tap(0.0) -> GET request(0.5) -> wait(1.0) -> POST request(1.2).
    assert (
        out.index(">#a<")
        < out.index('act-net">GET')
        < out.index('act-wait">wait')
        < out.index('act-net">POST')
    )


def test_html_exchanges_filtered_by_domain(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://api.example.com/x","status":200,"startedAt":0.2},'
        '{"method":"POST","url":"https://tracker.io/log","status":204,"startedAt":0.3}]',
        encoding="utf-8",
    )
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}],
        "network": {"filter": {"domains": ["example.com"]}},
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, started_at=0.0)],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path, definitions=[definition])
    # Only the example.com exchange is interleaved into the result timeline (its request
    # + response rows = two act-net badges)...
    assert out.count('class="act act-net"') == 2 and "https://api.example.com/x" in out
    # ...but the filtered-out request still appears in the (unfiltered) Network tab.
    assert "https://tracker.io/log" in out


def test_html_wait_request_detail_is_rich() -> None:
    # A `wait: { until: { request } }` step renders a tokenized detail (method / url /
    # status), in the same tone as other details — not a raw `{'request': ...}` dump.
    definition = {
        "name": "s1",
        "steps": [
            {"tap": {"id": "net.fetch"}},
            {
                "wait": {
                    "until": {
                        "request": {"method": "GET", "url": "https://example.com", "status": 200}
                    },
                    "timeout": 8,
                }
            },
        ],
        "expect": [],
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, started_at=0.1),
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r], definitions=[definition])
    assert "until request" in out
    assert '<span class="tk kw">GET</span>' in out
    assert '<span class="tk str">https://example.com</span>' in out
    assert 'status == <span class="tk num">200</span>' in out
    assert "{'request'" not in out  # not the raw python dict
