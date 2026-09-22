"""Acquisition-produced image preview configuration."""
from pathlib import PureWindowsPath
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PreviewGridConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    grid_type: Literal["row-by-row", "column-by-column", "snake-by-rows", "snake-by-columns"] = Field(
        default="column-by-column", description="Acquisition traversal. Snake reverses the fast direction on alternate strips; individual images are not flipped. Each strip still contains grid_size_y consecutive tiles.")
    order: Literal["right-down", "left-down", "right-up", "left-up", "down-right", "down-left", "up-right", "up-left"] = Field(
        default="down-right", description="Rows: right-down (top-left), left-down (top-right), right-up (bottom-left), left-up (bottom-right). Columns: down-right (top-left), down-left (top-right), up-right (bottom-left), up-left (bottom-right). First word is initial within-strip movement; second is movement between strips. Must match grid_type.")

    @model_validator(mode="after")
    def compatible_order(self):
        row_based = self.grid_type in {"row-by-row", "snake-by-rows"}
        valid = {"right-down", "left-down", "right-up", "left-up"} if row_based else {"down-right", "down-left", "up-right", "up-left"}
        if self.order not in valid:
            raise ValueError(f"{self.grid_type} requires order in {sorted(valid)}")
        return self


class EnfacePreviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    batch_enabled: bool = Field(default=True, description="Stitch each complete grid_size_y-tile batch and send previews to Slack when watch-enface runs.")
    acquisition_enabled: bool = Field(default=True, description="Also stitch the complete acquisition/mosaic and send previews to Slack.")
    aip_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_aip.nii", description="Exact filename format, not a glob. Required {image}; optional {slice}, {mosaic}, {acquisition}, {project}. Supports :04d zero-padding. Example: slice-{slice}_acq-{acquisition}_aip_{image:04d}.nii")
    mip_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_mip.nii", description="MIP filename format; same placeholders as aip_pattern. Set the actual suffix, including .nii.gz if used.")
    ori_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_ori.nii", description="Orientation filename format. Orientation is blended with circular averaging.")
    ret_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_ret.nii", description="Retardance filename format; no hardcoded modality suffix.")
    grid_config: PreviewGridConfig = Field(default_factory=PreviewGridConfig, description="linc-convert-compatible grid traversal and starting direction for acquisition previews. grid_size_y is tiles per strip; selected grid_size_x is number of strips, regardless of traversal axis.")
    first_image: int = Field(default=1, ge=0, description="First filename image index within this acquisition. Batches themselves remain 1-based.")
    orientation_units: Literal["degrees", "radians"] = Field(default="degrees", description="Units of acquisition orientation maps; radians are converted to degrees for linc-convert.")

    @model_validator(mode="before")
    @classmethod
    def migrate_traversal(cls, value):
        if isinstance(value, dict) and "traversal" in value:
            value = dict(value)
            traversal = value.pop("traversal")
            if traversal not in {"column-by-column", "snake-by-columns"}:
                raise ValueError("Invalid legacy preview traversal")
            value.setdefault("grid_config", {"grid_type": traversal, "order": "down-right"})
        return value

    @field_validator("aip_pattern", "mip_pattern", "ori_pattern", "ret_pattern")
    @classmethod
    def validate_pattern(cls, value):
        fields = []
        for _, field, spec, conversion in Formatter().parse(value):
            if field is None:
                continue
            if field not in {"image", "slice", "mosaic", "acquisition", "project"} or conversion:
                raise ValueError("Unsupported preview filename placeholder or conversion")
            fields.append(field)
        if "image" not in fields:
            raise ValueError("Preview filename must contain {image} (optional numeric format allowed)")
        rendered = value.format(image=1, slice=1, mosaic=1, acquisition="normal", project="test")
        if PureWindowsPath(rendered).name != rendered or rendered in {".", ".."}:
            raise ValueError("Preview patterns must be basenames, without directories")
        return value
