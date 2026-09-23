"""
DANDI upload tasks.

This module provides a list-capable uploader so higher-level flows can upload
multiple files in one Prefect task invocation.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Dict, List, Tuple

from prefect import get_run_logger, task
from prefect.blocks.system import Secret
from prefect_shell import ShellOperation

from opticstream.config.constants import (
    DANDI_API_TOKEN_BLOCK_NAME,
    LINC_API_TOKEN_BLOCK_NAME,
    DANDI_INSTANCE,
)


def build_dandi_upload_command(
    file_list: List[str],
    *,
    dandi_instance: DANDI_INSTANCE = "dandi",
    dandi_bin: str = "dandi",
    realpath: bool = True,
    max_jobs: str = "10:10",
) -> Tuple[str, str, str]:
    """
    Build the shell command to upload a list of files with the DANDI CLI.

    Returns
    -------
    (command, working_dir, api_key_env_var)
    """
    if not file_list:
        raise ValueError("file_list must not be empty")

    file_paths = [os.path.realpath(p) if realpath else p for p in file_list]
    file_paths_str = " ".join(shlex.quote(p) for p in file_paths)

    command = f"{shlex.quote(dandi_bin)} upload "
    if dandi_instance != "dandi":
        command += f"-i {shlex.quote(dandi_instance)} "

    command += f"{file_paths_str} -J {shlex.quote(max_jobs)}"
    if dandi_instance != "dandi":
        command += " --allow-any-path"
    command += " --existing overwrite --validation skip"

    # DANDI CLI errors can sometimes be clearer when run from file directory.
    working_dir = os.path.dirname(file_list[0]) if file_list[0] else os.getcwd()

    # Use the instance to decide which API key env var is expected.
    api_key_env_var = "LINC_API_KEY" if dandi_instance == "linc" else "DANDI_API_KEY"
    return command, working_dir, api_key_env_var


def _build_dandi_upload_env(
    *,
    dandi_instance: DANDI_INSTANCE,
    dandi_api_key: Secret | None = None,
) -> Dict[str, str]:
    """
    Build environment variables required by the DANDI/LINC CLI.

    Kept as a helper so both single-file and batch upload implementations
    share identical secret loading and env setup.
    """
    if dandi_api_key is not None:
        api_key = dandi_api_key.get()
    elif dandi_instance == "linc":
        api_key = Secret.load(LINC_API_TOKEN_BLOCK_NAME, validate=False).get()
    else:
        api_key = Secret.load(DANDI_API_TOKEN_BLOCK_NAME, validate=False).get()

    if dandi_instance == "linc":
        return {
            "LINC_API_KEY": api_key,
            "DANDI_API_KEY": api_key,
            "DANDI_DEVEL": "1",
        }

    return {
        "DANDI_API_KEY": api_key,
        "DANDI_DEVEL": "1",
    }


def _upload_dandi_api(
    file_list: List[str],
    *,
    max_jobs: str,
    dandi_api_key: Secret | None = None,
) -> None:
    """Upload DANDI assets through the API, including generic NIfTI files.

    Recent DANDI CLIs filter unrecognized files before upload and no longer
    provide the ``--allow-any-path`` switch.  The API equivalent is
    ``allow_any_path=True``.
    """
    from dandi.upload import UploadExisting, UploadValidation, upload

    try:
        jobs, jobs_per_file = (int(part) for part in max_jobs.split(":", 1))
    except (ValueError, AttributeError):
        jobs = jobs_per_file = 5

    paths = [Path(os.path.realpath(path)) for path in file_list]
    # DANDI needs the local dandiset metadata alongside assets in order to
    # resolve the target dataset.  Walk upward from each asset and include the
    # nearest dandiset.yaml once when it exists (e.g. .../001769/dandiset.yaml).
    metadata_paths: list[Path] = []
    for path in paths:
        for parent in (path.parent, *path.parents):
            metadata = parent / "dandiset.yaml"
            if metadata.is_file():
                if metadata not in metadata_paths:
                    metadata_paths.append(metadata)
                break

    # The Python DANDI API authenticates from DANDI_API_KEY.  Unlike the
    # subprocess path below, it does not receive the environment assembled by
    # _build_dandi_upload_env, so set the key explicitly for this call.  Keep
    # the change scoped to the upload and restore the worker environment after
    # the call completes.
    env = _build_dandi_upload_env(
        dandi_instance="dandi",
        dandi_api_key=dandi_api_key,
    )
    api_key = env["DANDI_API_KEY"]
    if not api_key:
        raise RuntimeError(
            "No DANDI API key is configured. Set the project's dandi_api_key "
            "Secret or create the 'dandi-api-key' Secret block."
        )

    previous_api_key = os.environ.get("DANDI_API_KEY")
    os.environ["DANDI_API_KEY"] = api_key
    try:
        upload(
            paths=[*paths, *metadata_paths],
            existing=UploadExisting.OVERWRITE,
            validation=UploadValidation.SKIP,
            dandi_instance="dandi",
            allow_any_path=True,
            jobs=jobs,
            jobs_per_file=jobs_per_file,
        )
    finally:
        if previous_api_key is None:
            os.environ.pop("DANDI_API_KEY", None)
        else:
            os.environ["DANDI_API_KEY"] = previous_api_key


@task(tags=["dandi-upload"], retries=1)
def upload_to_dandi_batch(
    file_list: List[str],
    *,
    dandi_instance: DANDI_INSTANCE = "dandi",
    dandi_bin: str = "dandi",
    realpath: bool = True,
    max_jobs: str = "10:10",
    dandi_api_key: Secret | None = None,
) -> None:
    """
    Upload multiple files to DANDI (or optionally to the LINC instance).
    """
    logger = get_run_logger()

    command, working_dir, _ = build_dandi_upload_command(
        file_list,
        dandi_instance=dandi_instance,
        dandi_bin=dandi_bin,
        realpath=realpath,
        max_jobs=max_jobs,
    )

    # Load secrets inside the task (not at import time).
    env = _build_dandi_upload_env(
        dandi_instance=dandi_instance,
        dandi_api_key=dandi_api_key,
    )

    logger.info("Running DANDI batch upload with %s files", len(file_list))
    logger.info(command)

    if dandi_instance == "dandi":
        _upload_dandi_api(
            file_list,
            max_jobs=max_jobs,
            dandi_api_key=dandi_api_key,
        )
        logger.info("DANDI batch upload completed")
        return

    with ShellOperation(
        commands=[command],
        env=env,
        working_dir=working_dir,
    ) as upload_operation:
        upload_process = upload_operation.trigger()
        upload_process.wait_for_completion()
        logger.info(upload_process.fetch_result())


@task(tags=["dandi-upload"], retries=1)
def upload_to_dandi(
    file_path: str,
    *,
    dandi_instance: DANDI_INSTANCE = "linc",
    dandi_bin: str = "dandi",
    realpath: bool = True,
    max_jobs: str = "10:10",
    dandi_api_key: Secret | None = None,
) -> None:
    """
    Upload the file to DANDI.
    """
    return upload_to_dandi_batch.fn(
        [file_path],
        dandi_instance=dandi_instance,
        dandi_bin=dandi_bin,
        realpath=realpath,
        max_jobs=max_jobs,
        dandi_api_key=dandi_api_key,
    )


__all__ = [
    "build_dandi_upload_command",
    "upload_to_dandi",
    "upload_to_dandi_batch",
]
