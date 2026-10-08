"""Per-user signed device build of the generic XCUITest runner (BE-0456).

A real iPhone or iPad only installs a runner signed by the person who runs it, so the device runner
cannot ship prebuilt the way the Simulator one does (BE-0292). This package builds it from the
runner sources — the checkout's own, or the copy the wheel carries — with the signing identity a
per-user file names, and caches the products under a key derived from every input that changes
them. ``signing`` owns that file, ``staging`` the scratch source tree and its per-user project spec,
and ``build`` the toolchain calls, the cache, and the run-time lookup.
"""
