from __future__ import annotations

from typing import Annotated, Literal

from cyclopts import App, Parameter
from opticstream.cli.oct import oct_cli
from opticstream.cli.state_common import (
    apply_mark_field,
    apply_mark_status,
    validate_mark_field_and_value,
)
from opticstream.state.oct_project_state import (
    OCTBatchState,
    OCTMosaicState,
    OCTSliceState,
    OCT_STATE_SERVICE,
)

oct_state_cli = oct_cli.command(App(name="state"))


@oct_state_cli.command
def show(
    project_name: str,
    *,
    slice: int | None = None,
    mosaic: int | None = None,
    batch: int | None = None,
    no_batches: Annotated[
        bool,
        Parameter(
            name=["--no-batches"],
            negative="",
            help="Omit batch entries, showing only slice and mosaic state.",
        ),
    ] = False,
) -> None:
    """
    Show OCT project state as pretty JSON.

    Examples:
    - opticstream oct state show myproject
    - opticstream oct state show myproject --no-batches
    - opticstream oct state show myproject --slice 1
    - opticstream oct state show myproject --slice 1 --mosaic 1
    - opticstream oct state show myproject --slice 1 --mosaic 1 --batch 1
    """
    if no_batches and batch is not None:
        raise ValueError("`--no-batches` cannot be used with `--batch`.")
    if batch is not None and mosaic is None:
        raise ValueError("`--mosaic` is required when `--batch` is provided.")
    if mosaic is not None and slice is None:
        raise ValueError("`--slice` is required when `--mosaic` is provided.")

    # Pydantic exclude specs that drop `batches` at whichever level is shown.
    exclude_batches = {"mosaics": {"__all__": {"batches"}}}
    exclude = None
    with OCT_STATE_SERVICE.open_project_by_parts(project_name=project_name) as project:
        if slice is None:
            view = project.to_view()
            exclude = {"slices": {"__all__": exclude_batches}}
        elif mosaic is None:
            slice_state = project.get_slice(slice)
            if slice_state is None:
                raise ValueError(f"Slice not found: slice={slice}")
            view = slice_state.to_view()
            exclude = exclude_batches
        elif batch is None:
            mosaic_state = project.get_mosaic(slice, mosaic)
            if mosaic_state is None:
                raise ValueError(f"Mosaic not found: slice={slice}, mosaic={mosaic}")
            view = mosaic_state.to_view()
            exclude = {"batches"}
        else:
            batch_state = project.get_batch(slice, mosaic, batch)
            if batch_state is None:
                raise ValueError(f"Batch not found: slice={slice}, mosaic={mosaic}, batch={batch}")
            view = batch_state.to_view()

    print(view.model_dump_json(indent=2, exclude=exclude if no_batches else None))


@oct_state_cli.command
def reset(
    project_name: str,
    *,
    reset_all: Annotated[bool, Parameter(name=["--all", "-a"])] = False,
    slice: int | None = None,
    mosaic: int | None = None,
    batch: int | None = None,
) -> None:
    """
    Delete a slice, mosaic, or batch from OCT project state.

    Examples:
    - opticstream oct state reset myproject --all
    - opticstream oct state reset myproject --slice 1
    - opticstream oct state reset myproject --slice 1 --mosaic 1
    - opticstream oct state reset myproject --slice 1 --mosaic 1 --batch 1
    """
    if reset_all:
        if slice is not None or mosaic is not None or batch is not None:
            raise ValueError("`--all` cannot be used with `--slice`, `--mosaic`, or `--batch`.")
    if slice is None and not reset_all:
        raise ValueError("`--slice` is required.")
    if batch is not None and mosaic is None:
        raise ValueError("`--mosaic` is required when `--batch` is provided.")

    with OCT_STATE_SERVICE.open_project_by_parts(project_name=project_name) as project:
        if mosaic is None:
            if reset_all:
                deleted = all(project.delete_slice(slice_id) for slice_id in list(project.slices))
                target = "all slices"
            else:
                deleted = project.delete_slice(slice)
                target = f"slice={slice}"
            hierarchy = "slice"
        elif batch is None:
            deleted = project.delete_mosaic(slice_id=slice, mosaic_id=mosaic)
            target = f"slice={slice}, mosaic={mosaic}"
            hierarchy = "mosaic"
        else:
            deleted = project.delete_batch(
                slice_id=slice,
                mosaic_id=mosaic,
                batch_id=batch,
            )
            target = f"slice={slice}, mosaic={mosaic}, batch={batch}"
            hierarchy = "batch"

    if not deleted:
        raise ValueError(
            "Target does not exist in project state: "
            f"project={project_name!r}, hierarchy={hierarchy!r}, {target}"
        )

    print(f"Reset {hierarchy} from project={project_name!r}: {target}")


MarkHierarchy = Literal["slice", "mosaic", "batch"]


@oct_state_cli.command
def mark(
    project_name: str,
    hierarchy: MarkHierarchy,
    field: str,
    value: str | None = None,
    *,
    slice: int | None = None,
    mosaic: int | None = None,
    batch: int | None = None,
) -> None:
    """
    Set OCT state values for a hierarchy target.

    Examples:
    - opticstream oct state mark myproject mosaic completed --slice 1
    - opticstream oct state mark myproject mosaic enface_uploaded true --slice 1
    - opticstream oct state mark myproject slice registered false --slice 1
    """
    if slice is None:
        raise ValueError("`--slice` is required.")
    if hierarchy == "batch" and mosaic is None:
        raise ValueError("`--mosaic` is required for `batch` hierarchy.")

    is_status = validate_mark_field_and_value(field, value)

    updated = 0
    with OCT_STATE_SERVICE.open_project_by_parts(project_name=project_name) as project:
        targets: list[OCTSliceState | OCTMosaicState | OCTBatchState] = []
        if hierarchy == "slice":
            slice_state = project.get_slice(slice)
            if slice_state is None:
                raise ValueError(f"Slice not found: slice={slice}")
            targets = [slice_state]
        elif hierarchy == "mosaic":
            if mosaic is None:
                slice_state = project.get_slice(slice)
                if slice_state is None:
                    raise ValueError(f"Slice not found: slice={slice}")
                targets = list(slice_state.mosaics.values())
                if not targets:
                    raise ValueError(f"No mosaics found in slice={slice}")
            else:
                mosaic_state = project.get_mosaic(slice, mosaic)
                if mosaic_state is None:
                    raise ValueError(f"Mosaic not found: slice={slice}, mosaic={mosaic}")
                targets = [mosaic_state]
        else:
            assert mosaic is not None
            if batch is None:
                mosaic_state = project.get_mosaic(slice, mosaic)
                if mosaic_state is None:
                    raise ValueError(f"Mosaic not found: slice={slice}, mosaic={mosaic}")
                targets = list(mosaic_state.batches.values())
                if not targets:
                    raise ValueError(f"No batches found: slice={slice}, mosaic={mosaic}")
            else:
                batch_state = project.get_batch(slice, mosaic, batch)
                if batch_state is None:
                    raise ValueError(
                        f"Batch not found: slice={slice}, mosaic={mosaic}, batch={batch}"
                    )
                targets = [batch_state]

        for target in targets:
            if is_status:
                apply_mark_status(target, field)  # type: ignore[arg-type]
            else:
                assert value is not None
                apply_mark_field(target, field, value)
            updated += 1

    print(
        f"Updated {updated} {hierarchy}(s) in project={project_name!r}: "
        f"field={field!r}, value={value!r}, slice={slice}, mosaic={mosaic}, batch={batch}"
    )
