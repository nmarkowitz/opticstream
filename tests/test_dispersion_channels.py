import numpy as np
import pytest

from opticstream.data_processing.psoct_recon.spectral2complex import (
    _load_dispersion_channels,
)


def test_load_dispersion_channels_splits_file_in_half(tmp_path) -> None:
    channel_1 = np.array([0.1, 0.2, 0.3], dtype=np.float64)
    channel_2 = np.array([1.1, 1.2, 1.3], dtype=np.float64)
    path = tmp_path / "dispersion.dat"
    np.concatenate((channel_1, channel_2)).tofile(path)

    actual_1, actual_2 = _load_dispersion_channels(path, samples_per_channel=3)

    np.testing.assert_array_equal(actual_1, channel_1)
    np.testing.assert_array_equal(actual_2, channel_2)


def test_load_dispersion_channels_rejects_wrong_length(tmp_path) -> None:
    path = tmp_path / "dispersion.dat"
    np.arange(5, dtype=np.float64).tofile(path)

    with pytest.raises(ValueError, match=r"expected 6 \(3 per channel\)"):
        _load_dispersion_channels(path, samples_per_channel=3)


def test_load_dispersion_channels_rejects_non_finite_values(tmp_path) -> None:
    path = tmp_path / "dispersion.dat"
    np.array([0.1, 0.2, np.nan, 1.1, 1.2, 1.3], dtype=np.float64).tofile(path)

    with pytest.raises(ValueError, match="contains non-finite values"):
        _load_dispersion_channels(path, samples_per_channel=3)
