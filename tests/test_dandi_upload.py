from prefect.blocks.system import Secret

from opticstream.tasks.dandi_upload import (
    _build_dandi_upload_env,
    build_dandi_upload_command,
)


def test_build_dandi_upload_command_defaults_to_dandi_instance() -> None:
    cmd, _, _ = build_dandi_upload_command(
        ["/tmp/file1.nii", "/tmp/file2.nii"],
        dandi_instance="dandi",
        realpath=False,
    )
    assert "-i" not in cmd
    assert "/tmp/file1.nii" in cmd
    assert "/tmp/file2.nii" in cmd


def test_build_dandi_upload_command_includes_linc_instance_flag() -> None:
    cmd, _, _ = build_dandi_upload_command(
        ["/tmp/file1.nii", "/tmp/file2.nii"],
        dandi_instance="linc",
        realpath=False,
    )
    assert "-i linc" in cmd
    assert "/tmp/file1.nii" in cmd
    assert "/tmp/file2.nii" in cmd


def test_selected_secret_is_used_for_dandi() -> None:
    env = _build_dandi_upload_env(
        dandi_instance="dandi",
        dandi_api_key=Secret(value="project-token"),
    )

    assert env["DANDI_API_KEY"] == "project-token"


def test_selected_secret_is_used_for_linc_compatibility_variables() -> None:
    env = _build_dandi_upload_env(
        dandi_instance="linc",
        dandi_api_key=Secret(value="project-token"),
    )

    assert env["LINC_API_KEY"] == "project-token"
    assert env["DANDI_API_KEY"] == "project-token"


def test_linc_uploads_are_refused() -> None:
    import inspect

    import pytest

    from opticstream.flows.lsm.strip_upload_flow import upload_strip_to_dandi_flow
    from opticstream.flows.psoct.mosaic_upload_flow import (
        upload_mosaic_enface_to_dandi_flow,
    )
    from opticstream.flows.psoct.mosaic_volume_upload_flow import (
        upload_mosaic_volume_to_dandi_flow,
    )
    from opticstream.tasks.dandi_upload import upload_to_dandi_batch

    with pytest.raises(ValueError, match="LINC uploads are deprecated"):
        upload_to_dandi_batch.fn(["/tmp/file.nii"], dandi_instance="linc")

    for flow in (
        upload_strip_to_dandi_flow,
        upload_mosaic_enface_to_dandi_flow,
        upload_mosaic_volume_to_dandi_flow,
    ):
        default = inspect.signature(flow.fn).parameters["dandi_instance"].default
        assert default == "dandi", flow.name
