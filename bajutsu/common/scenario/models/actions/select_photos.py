"""The `selectPhotos` action: pick images from PHPickerViewController's grid, by ordinal position."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from bajutsu.common.scenario.models._base import _Model


class SelectPhotos(_Model):
    """`selectPhotos` action — pick one or more images from an open `PHPickerViewController` grid.

    iOS-only (`Capability.SELECT_PHOTOS`, device / Intel Simulator; see `capabilities_for_run`):
    every grid cell shares one identifier (`PXGGridLayout-Info`), so `indices` names each cell by its
    ordinal position — the same "nth of multiple matches" mechanism `handleSystemAlert` already
    relies on for a SpringBoard button no author-assignable identifier ever names. A cell carries no
    label an author could ask for by name, only its position in the grid the picker renders, so no
    `sel` field is offered here the way `tap` or `setPickerValue` take one.

    `timeout` bounds the condition wait for the grid (the picker must already be open) and for the
    confirm control that follows the last tap.
    """

    indices: list[int]
    timeout: float

    @model_validator(mode="after")
    def _indices_shape(self) -> Self:
        if not self.indices:
            raise ValueError("selectPhotos indices must not be empty (§6.2)")
        if any(i < 0 for i in self.indices):
            raise ValueError("selectPhotos indices must be non-negative (§6.2)")
        if len(set(self.indices)) != len(self.indices):
            raise ValueError("selectPhotos indices must not repeat the same cell (§6.2)")
        return self
