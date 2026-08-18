#!/usr/bin/env python3
"""AWS Device Farm batch submitter CLI (BE-0235; iOS support BE-0238).

A thin command-line wrapper over the submitter core in `bajutsu.cloud.devicefarm` (moved there by
BE-0336 so the core is coverage-measured and reusable by serve's fan-out). This file keeps only the
argparse glue and the two real-IO adapters that fill the core's `DeviceFarmClient` / `Transfer`
seams — the real boto3 client and the presigned-URL transfer — which do live network/AWS I/O and so
are exercised end to end rather than unit-tested against the in-memory fake. Everything the fake
covers (spec rendering, packaging, verdict, the upload/poll/collect state machine) is in the core.

The verdict still comes from **Bajutsu's own manifest**, never from Device Farm's run
classification, and the whole flow stays outside the deterministic `run`/CI verdict path.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from bajutsu.cloud.devicefarm import (
    APP_UPLOAD_TYPE,
    REQUIREMENTS_TXT,
    DeviceFarmClient,
    DeviceFarmError,
    HttpTransfer,
    build_package,
    render_test_spec,
    submit_and_collect,
)


def _devicefarm_client() -> DeviceFarmClient:
    """Build the real boto3 ``devicefarm`` client (lazy import — the ``aws`` extra is optional)."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise DeviceFarmError(
            "the AWS Device Farm submitter needs boto3 — install it with `uv sync --extra aws`"
        ) from exc
    # Device Farm's control plane lives only in us-west-2. boto3's dynamically built client is
    # untyped, so present it as the DeviceFarmClient slice we actually use.
    return cast("DeviceFarmClient", boto3.client("devicefarm", region_name="us-west-2"))


def main(argv: Sequence[str] | None = None) -> int:
    """Package the payload and, unless ``--package-only``, submit it and print Bajutsu's verdict."""
    parser = argparse.ArgumentParser(
        description="Submit Bajutsu scenarios (Android or iOS) to AWS Device Farm."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        required=True,
        help="scenario path inside the package (repeat for several)",
    )
    parser.add_argument("--target", required=True, help="targets.<name> config entry")
    parser.add_argument("--config", required=True, help="Bajutsu config path inside the package")
    parser.add_argument(
        "--platform",
        choices=["android", "ios"],
        default="android",
        help="reserved-device platform (selects the backend, --udid, and app upload type)",
    )
    parser.add_argument(
        "--app",
        "--app-apk",
        dest="app",
        type=Path,
        required=True,
        help="the app artifact to install (Android .apk or iOS .ipa)",
    )
    parser.add_argument(
        "--package",
        required=True,
        help="entry as source=arcname (repeat)",
        action="append",
        dest="package_entries",
        metavar="SRC=ARCNAME",
        default=[],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("devicefarm-package.zip"),
        help="where to write the test package zip",
    )
    parser.add_argument("--python-version", default="3.13")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="build the package and spec, but do not submit (no AWS credentials needed)",
    )
    parser.add_argument(
        "--project-arn", help="Device Farm project ARN (required unless --package-only)"
    )
    parser.add_argument(
        "--device-pool-arn", help="Device Farm device-pool ARN (required unless --package-only)"
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path("devicefarm-artifacts"),
        help="where to download the run artifacts",
    )
    args = parser.parse_args(argv)

    spec = render_test_spec(
        args.scenarios,
        target=args.target,
        config=args.config,
        platform=args.platform,
        python_version=args.python_version,
    )
    spec_path = args.out.parent / "testspec.yml"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec, encoding="utf-8")

    entries: list[tuple[Path, str]] = []
    for raw in args.package_entries:
        src, _, arcname = raw.partition("=")
        entries.append((Path(src), arcname or Path(src).name))
    build_package(entries, args.out, extra_texts={"requirements.txt": REQUIREMENTS_TXT})
    print(f"wrote package {args.out} and spec {spec_path}")

    if args.package_only:
        return 0
    if not args.project_arn or not args.device_pool_arn:
        parser.error("--project-arn and --device-pool-arn are required unless --package-only")

    verdict = submit_and_collect(
        _devicefarm_client(),
        HttpTransfer(),
        project_arn=args.project_arn,
        device_pool_arn=args.device_pool_arn,
        app_path=args.app,
        package_zip=args.out,
        spec_yaml=spec_path,
        dest=args.dest,
        app_upload_type=APP_UPLOAD_TYPE[args.platform],
    )
    print(f"bajutsu verdict: {'PASS' if verdict.ok else 'FAIL'} ({verdict.passed}/{verdict.total})")
    if verdict.failures:
        print("failed scenarios: " + ", ".join(verdict.failures))
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
