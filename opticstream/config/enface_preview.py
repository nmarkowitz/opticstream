"""Acquisition-produced image preview configuration."""
from pathlib import PureWindowsPath
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EnfacePreviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    batch_enabled: bool = Field(default=True, description="Stitch each complete grid_size_y-tile batch and send previews to Slack when watch-enface runs.")
    acquisition_enabled: bool = Field(default=True, description="Also stitch the complete acquisition/mosaic and send previews to Slack.")
    aip_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_aip.nii", description="Exact filename format, not a glob. Required {image}; optional {slice}, {mosaic}, {acquisition}, {project}. Supports :04d zero-padding. Example: slice-{slice}_acq-{acquisition}_aip_{image:04d}.nii")
    mip_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_mip.nii", description="MIP filename format; same placeholders as aip_pattern. Set the actual suffix, including .nii.gz if used.")
    ori_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_ori.nii", description="Orientation filename format. Orientation is blended with circular averaging.")
    ret_pattern: str = Field(default="mosaic_{mosaic:03d}_image_{image:04d}_ret.nii", description="Retardance filename format; no hardcoded modality suffix.")
    traversal: Literal["column-by-column", "snake-by-columns"] = Field(default="column-by-column", description="Each batch advances along Y; batches advance along X. Snake reverses Y placement in alternate batches without flipping individual images.")
    first_image: int = Field(default=1, ge=0, description="First filename image index within this acquisition. Batches themselves remain 1-based.")
    orientation_units: Literal["degrees", "radians"] = Field(default="degrees", description="Units of acquisition orientation maps; radians are converted to degrees for linc-convert.")

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
