"""BE-0510: what a run zip must look like to be accepted, and what of it is kept."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest
from _run_import import (
    RUN_ID,
    SCENARIO_YAML,
    manifest,
    minimal_files,
    real_run,
    run_zip,
    zip_files,
)

from bajutsu.serve.uploads import BundleError, run_bundle
from bajutsu.serve.uploads.run_bundle import ImportedRun, unpack_run


def _unpack(zip_path: Path, tmp_path: Path) -> ImportedRun:
    staging = tmp_path / "staging"
    staging.mkdir()
    return unpack_run(zip_path, staging)


def test_a_real_run_tree_imports_and_drops_its_raw_xml(tmp_path: Path) -> None:
    run_dir = real_run(tmp_path / "runs")
    xml = sorted(p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*.xml"))
    assert [Path(p).name for p in xml if p != "junit.xml"] == [
        "hierarchy.parsed-input.xml",
        "hierarchy.raw.xml",
    ]

    imported = _unpack(run_zip(run_dir, tmp_path / "run.zip"), tmp_path)

    assert imported.run_id == RUN_ID
    assert imported.dropped == [p for p in xml if p != "junit.xml"]
    stored = json.loads((imported.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert stored["provenance"]["source"] == "import"
    assert stored["provenance"]["scenarioHash"].startswith("sha256:")
    assert (imported.run_dir / "report.html").is_file()
    assert (imported.run_dir / "junit.xml").is_file()
    assert (imported.run_dir / "ctrf.json").is_file()
    assert list(imported.run_dir.rglob("*.png"))


def test_the_uploaded_report_files_are_replaced_by_the_servers_own(tmp_path: Path) -> None:
    files = minimal_files(
        **{"report.html": "<script>theirs</script>", "ctrf.json": '"theirs"', "junit.xml": "theirs"}
    )

    imported = _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)

    for name in ("report.html", "ctrf.json", "junit.xml"):
        assert "theirs" not in (imported.run_dir / name).read_text(encoding="utf-8")
    # Regenerated rather than dropped, so none of the three is reported as dropped.
    assert imported.dropped == []


def test_active_file_types_are_dropped_in_any_letter_case(tmp_path: Path) -> None:
    files = minimal_files(
        **{
            "a/page.HTML": "x",
            "a/icon.Svg": "x",
            "a/doc.xml": "x",
            "a/noextension": "x",
            "a/shot.PNG": "x",
        }
    )

    imported = _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)

    assert imported.dropped == ["a/doc.xml", "a/icon.Svg", "a/noextension", "a/page.HTML"]
    assert (imported.run_dir / "a" / "shot.PNG").is_file()
    assert not (imported.run_dir / "a" / "page.HTML").exists()


def _scenarios(*artifacts: str, kind: str = "video") -> str:
    return manifest(
        scenarios=[
            {
                "scenario": "a",
                "ok": True,
                "artifacts": [{"name": n, "kind": kind, "provider": "x"} for n in artifacts],
            }
        ]
    )


def test_an_artifact_the_type_filter_drops_is_not_a_rejection(tmp_path: Path) -> None:
    files = minimal_files(**{"s/raw.xml": "<x/>"})
    files[f"{RUN_ID}/manifest.json"] = _scenarios("s/raw.xml")

    imported = _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)

    assert imported.dropped == ["s/raw.xml"]


@pytest.mark.parametrize(
    "path",
    [
        "javascript:alert(1)",
        "https://example.com/a.png",
        "//example.com/a.png",
        "/etc/passwd",
        "../other/a.png",
        "s/../../a.png",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "s\\a.png",
        "s/not-in-the-zip.png",
    ],
)
def test_an_artifact_path_that_leaves_the_run_is_rejected(tmp_path: Path, path: str) -> None:
    files = minimal_files()
    files[f"{RUN_ID}/manifest.json"] = _scenarios(path)

    with pytest.raises(BundleError, match="artifact"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


@pytest.mark.parametrize(
    "document",
    [
        {"visual": {"actual": "javascript:alert(1)"}},
        {"visual": {"actual": "a.png", "diff": "/x"}},
        {"provenance": {"hooks": [{"log": "https://example.com"}]}},
    ],
)
def test_every_linking_field_is_checked(tmp_path: Path, document: dict[str, object]) -> None:
    files = minimal_files(**{"a.png": "x"})
    files[f"{RUN_ID}/manifest.json"] = manifest(**document)

    with pytest.raises(BundleError, match="artifact"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_a_scenario_result_json_is_checked_like_the_manifest(tmp_path: Path) -> None:
    result = json.dumps({"scenario": {"artifacts": [{"name": "../x.png"}]}})
    files = minimal_files(**{"s/result.json": result})

    with pytest.raises(BundleError, match="artifact"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_an_engine_pass_result_json_names_files_from_its_engine_directory(tmp_path: Path) -> None:
    # A `--browsers` run's manifest is re-rooted under `<engine>/`; its `result.json` files are not.
    result = json.dumps({"scenario": {"artifacts": [{"name": "00-a/after.png"}]}})
    files = minimal_files(**{"chromium/00-a/result.json": result, "chromium/00-a/after.png": "x"})
    files[f"{RUN_ID}/manifest.json"] = _scenarios("chromium/00-a/after.png")

    assert _unpack(zip_files(tmp_path / "run.zip", files), tmp_path).dropped == []


def _without(name: str) -> dict[str, str | bytes]:
    files = minimal_files()
    del files[f"{RUN_ID}/{name}"]
    files[f"{RUN_ID}/other.txt"] = "x"
    return files


def _with(name: str, data: str) -> dict[str, str | bytes]:
    return {**minimal_files(), f"{RUN_ID}/{name}": data}


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (_without("manifest.json"), "manifest.json"),
        (_with("manifest.json", "{nope"), "manifest.json"),
        (_with("manifest.json", "[]"), "boolean `ok`"),
        (_with("manifest.json", '{"ok": 1, "scenarios": []}'), "boolean `ok`"),
        (_with("manifest.json", '{"ok": true}'), "list `scenarios`"),
        (_with("manifest.json", manifest(runId="20260101-000000")), "different run"),
        (_with("manifest.json", manifest(scenarios=[1])), "cannot be rendered"),
        (_without("scenario.yaml"), "scenario.yaml"),
        (_with("scenario.yaml", "{unclosed"), "not readable YAML"),
        (_with("scenario.yaml", "- &a [1]\n- [*a, *a]\n"), "YAML aliases"),
        (_with(".deleted", "x"), "trashed"),
        (_with("a/.DELETED", "x"), "trashed"),
        (_with("../escape.txt", "x"), "unsafe entry path"),
        ({**minimal_files(), "other-run/a.txt": "x"}, "exactly one top-level directory"),
        ({**minimal_files(), "loose.txt": "x"}, "under the run's directory"),
        (minimal_files(run_id=".hidden"), "not a run id"),
        ({}, "exactly one top-level directory"),
    ],
)
def test_a_malformed_run_zip_is_rejected(
    tmp_path: Path, files: dict[str, str | bytes], message: str
) -> None:
    with pytest.raises(BundleError, match=message):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_a_body_that_is_no_zip_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "run.zip").write_bytes(b"not a zip")

    with pytest.raises(BundleError, match="not a valid zip"):
        _unpack(tmp_path / "run.zip", tmp_path)


def test_a_symlink_entry_is_rejected(tmp_path: Path) -> None:
    path = zip_files(tmp_path / "run.zip", minimal_files())
    link = zipfile.ZipInfo(f"{RUN_ID}/link.png")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(link, "/etc/passwd")

    with pytest.raises(BundleError, match="symlink"):
        _unpack(path, tmp_path)


@pytest.mark.parametrize("kind", ["deviceLog", "appTrace", "network", "elements"])
def test_a_report_that_would_embed_too_much_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # One small file linked many times is embedded once per link, so the links are what is summed,
    # by kind: the renderer reads such a file whatever it is called.
    monkeypatch.setattr(run_bundle, "MAX_EMBEDDED_BYTES", 100)
    files = minimal_files(**{"s/disguised.png": "[]" + " " * 40})
    files[f"{RUN_ID}/manifest.json"] = _scenarios(*["s/disguised.png"] * 3, kind=kind)

    with pytest.raises(BundleError, match="would embed"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


@pytest.mark.parametrize("kind", ["screenshot", "video"])
def test_a_linked_only_artifact_does_not_count_toward_the_report_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(run_bundle, "MAX_EMBEDDED_BYTES", 100)
    files = minimal_files(**{"s/big.mp4": "x" * 400})
    files[f"{RUN_ID}/manifest.json"] = _scenarios("s/big.mp4", "s/big.mp4", kind=kind)

    assert _unpack(zip_files(tmp_path / "run.zip", files), tmp_path).run_id == RUN_ID


def test_a_plan_repeated_for_every_result_counts_toward_the_report_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The report embeds a scenario's plan once per result, so many results of one small plan add up.
    monkeypatch.setattr(run_bundle, "MAX_EMBEDDED_BYTES", 2000)
    result = {"scenario": "a", "ok": True, "steps": []}
    files = minimal_files()
    files[f"{RUN_ID}/manifest.json"] = manifest(scenarios=[result])
    assert _unpack(zip_files(tmp_path / "run.zip", files), tmp_path).run_id == RUN_ID

    files[f"{RUN_ID}/manifest.json"] = manifest(scenarios=[result] * 100)
    (tmp_path / "again").mkdir()
    with pytest.raises(BundleError, match="would embed"):
        _unpack(zip_files(tmp_path / "again.zip", files), tmp_path / "again")


@pytest.mark.parametrize("duration", [10**400, 1e308])
def test_a_number_the_renderer_cannot_hold_is_a_rejection(tmp_path: Path, duration: float) -> None:
    step = {"index": 0, "action": "tap", "ok": True, "started_at": 0, "duration_s": duration}
    files = minimal_files()
    files[f"{RUN_ID}/manifest.json"] = manifest(
        scenarios=[{"scenario": "a", "ok": True, "steps": [step]}]
    )

    with pytest.raises(BundleError, match="cannot be rendered"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_an_oversized_manifest_is_rejected_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_bundle, "MAX_MANIFEST_BYTES", 10)

    with pytest.raises(BundleError, match=r"manifest\.json is over"):
        _unpack(zip_files(tmp_path / "run.zip", minimal_files()), tmp_path)


def test_an_artifact_kind_that_is_no_string_is_a_rejection_not_a_crash(tmp_path: Path) -> None:
    artifact = {"name": "a.txt", "kind": [], "provider": "x"}
    files = minimal_files(**{"a.txt": "x"})
    files[f"{RUN_ID}/manifest.json"] = manifest(scenarios=[{"artifacts": [artifact]}])

    with pytest.raises(BundleError, match="cannot be rendered"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_an_oversized_result_json_is_rejected_before_it_is_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_bundle, "MAX_MANIFEST_BYTES", 200)
    files = minimal_files(**{"s/result.json": "[" + "0," * 200 + "0]"})

    with pytest.raises(BundleError, match=r"result\.json is over"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_deeply_nested_json_is_rejected_rather_than_crashing(tmp_path: Path) -> None:
    files = minimal_files()
    nested = "[" * 5000 + "]" * 5000
    files[f"{RUN_ID}/manifest.json"] = f'{{"ok": true, "scenarios": [], "x": {nested}}}'

    with pytest.raises(BundleError, match=r"nested|readable"):
        _unpack(zip_files(tmp_path / "run.zip", files), tmp_path)


def test_a_run_too_large_uncompressed_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_bundle, "MAX_TOTAL_BYTES", len(SCENARIO_YAML))

    with pytest.raises(BundleError, match="run is too large") as caught:
        _unpack(zip_files(tmp_path / "run.zip", minimal_files()), tmp_path)

    assert "bomb" not in str(caught.value)
