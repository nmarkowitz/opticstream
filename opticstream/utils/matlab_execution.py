"""
MATLAB batch command execution utilities.

Centralizes MATLAB subprocess execution with consistent error handling.
Includes MATLAB engine initialization and command-line fallback utilities.
"""

from pathlib import Path
from typing import Optional

from prefect.logging import get_run_logger
from prefect_shell import ShellOperation

import psoct_toolbox


def resolve_matlab_root(override: Optional[str] = None) -> str:
    """
    Resolve the MATLAB toolbox root, using an explicit override when provided.

    Parameters
    ----------
    override :
        Optional explicit filesystem path. When provided, it is used directly;
        otherwise the path is obtained from psoct_toolbox.get_matlab_root().
    """
    if override:
        return str(Path(override).expanduser())
    try:
        return psoct_toolbox.get_matlab_root()
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            "Failed to determine MATLAB toolbox root from psoct_toolbox. "
            "Ensure the psoct_toolbox package is installed and "
            "get_matlab_root() is available."
        ) from exc


def _is_matlab_engine_available() -> bool:
    """
    Return True if the MATLAB Engine for Python package is importable.
    """
    try:
        import matlab.engine  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def run_matlab_batch_command_or_cli(
    command: str,
    matlab_script_path: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> None:
    """
    Run a MATLAB batch command via the Engine when available, else ``matlab -batch``.

    Uses ``addpath(genpath(root));`` as preamble in both the engine and CLI paths.

    Parameters
    ----------
    command
        MATLAB command string to execute (e.g. ``"spectral2complex_batch(...)"``).
    matlab_script_path
        Optional toolbox root override; passed to :func:`resolve_matlab_root`.
    working_dir
        Optional working directory for the CLI subprocess.
    """
    logger = get_run_logger()
    resolved_path = resolve_matlab_root(matlab_script_path)
    bundled_path = Path(__file__).resolve().parents[1] / "matlab"
    root_literal = str(resolved_path).replace("'", "''")
    bundled_literal = str(bundled_path).replace("'", "''")
    matlab_cmd = (
        f"addpath(genpath('{root_literal}'));"
        f"addpath('{bundled_literal}', '-begin');{command}"
    )
    logger.info(f"MATLAB command: {matlab_cmd}")
    if _is_matlab_engine_available():
        import matlab.engine  # type: ignore[import-not-found]

        eng = matlab.engine.start_matlab()
        logger.info(f"MATLAB engine started; toolbox path added: {resolved_path}")
        try:
            eng.eval(matlab_cmd, nargout=0)
            logger.info("MATLAB batch command completed via MATLAB engine")
        finally:
            eng.quit()
        return

    logger.warning("MATLAB Engine not available, using CLI batch")
    escaped_cmd = matlab_cmd.replace('"', '\\"')
    logger.info(f'Full MATLAB command: matlab -batch "{escaped_cmd}"')
    with ShellOperation(
        commands=[f'matlab -batch "{escaped_cmd}"'],
        working_dir=working_dir,
    ) as op:
        proc = op.trigger()
        proc.wait_for_completion()
        if proc.return_code != 0:
            raise RuntimeError(f"MATLAB command failed: {proc.fetch_result()}")
        logger.info(f"MATLAB execution output: {proc.fetch_result()}")
