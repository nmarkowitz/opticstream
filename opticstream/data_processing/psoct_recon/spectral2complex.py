import os
import re
import time

import nibabel as nib
import numpy as np
from scipy import interpolate
from scipy.io import loadmat

# -----------------------
# === CONFIG CONSTANTS ===
# -----------------------
# Hardcode your calibration .mat path here (must contain 'mu' Nx2 and 'wave_prime')
CALIB_MAT_PATH = (
    "/autofs/cluster/octdata2/users/calibration_nov21/calibration/wave-pixel.mat"
)


# -----------------------
# === Utility I/O funcs ===
# -----------------------


def load_spectral_file_raw(
    file_path, aline_length=200, bline_length=350, nifti_header_size=352
):
    file = open(file_path, "rb")
    file.seek(nifti_header_size)
    data = np.fromfile(file, np.uint16, aline_length * bline_length * 4096)
    data = data.reshape((bline_length, 2, 2048, aline_length))
    file.close()
    return data


def read_spectral_file_raw(filename, header_bytes=352):
    """
    Read binary spectral file and return raw uint16 array (no reshaping).
    """
    if not os.path.exists(filename):
        raise FileNotFoundError(filename)
    with open(filename, "rb") as f:
        f.seek(header_bytes, os.SEEK_SET)
        raw = f.read()
    return np.frombuffer(raw, dtype=np.uint16)


def _load_dispersion_channels(filename, samples_per_channel):
    """Load two consecutive float64 phase vectors from one binary file."""
    phase_dispersion = np.fromfile(filename, dtype=np.float64)
    expected_values = 2 * samples_per_channel
    if phase_dispersion.size != expected_values:
        raise ValueError(
            f"Dispersion file {filename!s} has {phase_dispersion.size} values; "
            f"expected {expected_values} ({samples_per_channel} per channel)."
        )
    if not np.isfinite(phase_dispersion).all():
        raise ValueError(f"Dispersion file {filename!s} contains non-finite values.")

    return (
        phase_dispersion[:samples_per_channel],
        phase_dispersion[samples_per_channel:],
    )


def save_jstack_nifti(Jstack_all, output_file):
    """
    Save Jstack_all to a NIfTI file (float32).
    """
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    img = nib.Nifti1Image(Jstack_all.astype(np.float32), affine=np.eye(4))
    nib.save(img, output_file)


# -----------------------
# === Ported functions ===
# -----------------------
def Buffer2Jones(OriginalBuffer, PaddingFactor, AutoCorrPeakCut):
    """
    Ported from MATLAB Buffer2Jones.
    OriginalBuffer shape: (L, Aline)
    Returns: Jones of shape (Depth, Aline)
    """
    L = OriginalBuffer.shape[0]
    # MATLAB computed AlineLength = size(OriginalBuffer,1) / (2*PaddingFactor)
    AlineLength = int(L / (2 * PaddingFactor))
    # FFT along axis 0, length L
    Jones = np.fft.fft(OriginalBuffer, n=L, axis=0)
    # Keep only first AlineLength rows (MATLAB indexing 1:AlineLength)
    Jones = Jones[:AlineLength, :]
    # Remove autocorrelation peak rows 1:AutoCorrPeakCut (MATLAB indexing)
    if AutoCorrPeakCut > 0:
        Jones = Jones[AutoCorrPeakCut:, :]
    return Jones


def interpolationwave(Parameters, calib_mat_path=CALIB_MAT_PATH):
    """
    Ported from MATLAB interpolationwave_101620; uses hardcoded calib_mat_path by default.
    Parameters = [PaddingFactor, PaddingLength, OriginalLineLength1, Start1, OriginalLineLength2, Start2]
    Returns:
      Wavelengths_l, Wavelengths_r, InterpolatedWavelengths, Ks, Ko1, Ko2
    """
    PaddingFactor = Parameters[0]
    PaddingLength = Parameters[1]
    OriginalLineLength1 = Parameters[2]
    Start1 = Parameters[3]
    OriginalLineLength2 = Parameters[4]
    Start2 = Parameters[5]

    # Load calibration .mat (expects 'mu' Nx2 and 'wave_prime' or 'wave_sample')
    m = loadmat(calib_mat_path)
    if "mu" not in m:
        raise KeyError(f"Calibration .mat missing 'mu'. Keys: {list(m.keys())}")
    mu = m["mu"]
    if "wave_prime" in m:
        wave_sample = m["wave_prime"].squeeze()
    elif "wave_sample" in m:
        wave_sample = m["wave_sample"].squeeze()
    else:
        raise KeyError("Calibration .mat missing 'wave_prime'/'wave_sample'")

    position_l = mu[:, 0].squeeze()
    position_r = mu[:, 1].squeeze()
    wave_sample = np.asarray(wave_sample).squeeze()
    if wave_sample.shape != position_l.shape:
        wave_sample = wave_sample.reshape(position_l.shape)

    # Linear fit (polyfit degree 1) and evaluate 1:2048
    pcoeff1 = np.polyfit(position_l, wave_sample, 1)
    pcoeff2 = np.polyfit(position_r, wave_sample, 1)
    x1 = np.arange(1, 2048 + 1)
    x2 = np.arange(1, 2048 + 1)
    lamda_l = np.polyval(pcoeff1, x1)
    lamda_r = np.polyval(pcoeff2, x2)

    W_l = lamda_l[Start1 - 1 : Start1 - 1 + OriginalLineLength1]
    W_r = lamda_r[Start2 - 1 : Start2 - 1 + OriginalLineLength2]

    xx1 = np.linspace(Start1, Start1 + OriginalLineLength1 - 1, PaddingLength)
    xx2 = np.linspace(Start2, Start2 + OriginalLineLength2 - 1, PaddingLength)

    Wavelengths_l = 1e-9 * np.interp(
        xx1, np.arange(Start1, Start1 + OriginalLineLength1), W_l
    )
    Wavelengths_r = 1e-9 * np.interp(
        xx2, np.arange(Start2, Start2 + OriginalLineLength2), W_r
    )

    minK = 2.0 * np.pi / min(Wavelengths_l[-1], Wavelengths_r[-1])
    maxK = 2.0 * np.pi / max(Wavelengths_l[0], Wavelengths_r[0])
    Ks = np.flip(np.linspace(minK, maxK, PaddingLength))
    InterpolatedWavelengths = (2.0 * np.pi) / Ks

    Ko1 = (2.0 * np.pi) / Wavelengths_l
    Ko2 = (2.0 * np.pi) / Wavelengths_r

    return Wavelengths_l, Wavelengths_r, InterpolatedWavelengths, Ks, Ko1, Ko2


# -----------------------
# === Core processing ===
# -----------------------
def spectral2complex(
    spectral_array,
    disp_comp_file=None,
    AlineLength=2048,
    AutoCorrPeakCut=24,
    PaddingFactor=1,
):
    """
    Process pre-reshaped spectral_array into Jstack_all and return it.

    Input:
      - spectral_array: MUST be shaped as (AlineLength, Aline, 2, Bline)
                        (dtype can be uint16 or numeric; will be cast to float64)
      - disp_comp_file: optional binary float64 dispersion correction file path.
                        The first AlineLength values apply to channel 1 and
                        the second AlineLength values apply to channel 2.
      - AlineLength, AutoCorrPeakCut, PaddingFactor: processing parameters (kept for compatibility)
    Output:
      - Jstack_all: numpy array shaped (4*Aline, Bline, Depth)
    """
    t0 = time.time()

    arr = np.asarray(spectral_array)
    if arr.ndim != 4:
        raise ValueError(
            "spectral_array must be 4-D shaped (AlineLength, Aline, 2, Bline)."
        )
    if arr.shape[0] != AlineLength:
        raise ValueError(f"First dimension must equal AlineLength={AlineLength}.")

    # Unpack dims
    _, Aline, channels, Bline = arr.shape
    if channels != 2:
        raise ValueError("Third dimension must be 2 (two channels).")

    # Extract channel buffers; follow MATLAB order: channel1 = arr[:,:,0,:], channel2 = arr[:,:,1,:] and flip channel2 along first axis
    WavelengthBuffer1_all = arr[:, :, 0, :].astype(np.float64)
    WavelengthBuffer2_all = arr[:, :, 1, :].astype(np.float64)
    for k in range(Bline):
        WavelengthBuffer2_all[:, :, k] = np.flipud(WavelengthBuffer2_all[:, :, k])

    # Basic derived params
    PaddingLength = AlineLength * PaddingFactor
    OriginalLineLength1 = AlineLength
    OriginalLineLength2 = AlineLength
    InterpolationParameters = [
        PaddingFactor,
        PaddingLength,
        OriginalLineLength1,
        1,
        OriginalLineLength2,
        1,
    ]  # Start1/Start2 always 1

    # Get wavelengths from hardcoded calibration .mat
    Wavelengths_l, Wavelengths_r, InterpolatedWavelengths2, Ks, Ko1, Ko2 = (
        interpolationwave(InterpolationParameters, calib_mat_path=CALIB_MAT_PATH)
    )
    newLen = InterpolatedWavelengths2.shape[0]

    # Load the channel-specific dispersion corrections.
    if disp_comp_file is not None:
        phaseDispersion1, phaseDispersion2 = _load_dispersion_channels(
            disp_comp_file, AlineLength
        )
    else:
        phaseDispersion1 = phaseDispersion2 = np.zeros(PaddingLength, dtype=np.float64)

    # Preallocate Jones stacks: Bline x Aline x Depth
    DepthL = 1024 - AutoCorrPeakCut
    Jones1_3D = np.zeros((Bline, Aline, DepthL), dtype=np.complex128)
    Jones2_3D = np.zeros((Bline, Aline, DepthL), dtype=np.complex128)

    # Mean subtraction across Aline
    refdata1_all = np.mean(WavelengthBuffer1_all, axis=1, keepdims=True)
    refdata2_all = np.mean(WavelengthBuffer2_all, axis=1, keepdims=True)
    MeanScan1_all = WavelengthBuffer1_all - refdata1_all
    MeanScan2_all = WavelengthBuffer2_all - refdata2_all

    # Extract original buffers (Start=1 -> indices 0:OriginalLineLength)
    OriginalBuffer1_all = MeanScan1_all[0:OriginalLineLength1, :, :]
    OriginalBuffer2_all = MeanScan2_all[0:OriginalLineLength2, :, :]

    # ZeroPadBuffer simple implementation (replace if you have different rules)
    def ZeroPadBuffer_simple(buf2d, pad_factor):
        if pad_factor == 1:
            return buf2d.copy()
        L_orig, A = buf2d.shape
        L_pad = L_orig * pad_factor
        out = np.zeros((L_pad, A), dtype=buf2d.dtype)
        out[:L_orig, :] = buf2d
        return out

    ZeroPaddedBuffer1_all = np.zeros((PaddingLength, Aline, Bline), dtype=np.float64)
    ZeroPaddedBuffer2_all = np.zeros((PaddingLength, Aline, Bline), dtype=np.float64)
    for k in range(Bline):
        ZeroPaddedBuffer1_all[:, :, k] = ZeroPadBuffer_simple(
            OriginalBuffer1_all[:, :, k], PaddingFactor
        )
        ZeroPaddedBuffer2_all[:, :, k] = ZeroPadBuffer_simple(
            OriginalBuffer2_all[:, :, k], PaddingFactor
        )

    # Interpolation: reshape to 2D (L_in x (Aline*Bline))
    L_in = ZeroPaddedBuffer1_all.shape[0]
    Z1_2D = ZeroPaddedBuffer1_all.reshape(L_in, Aline * Bline, order="F")
    Z2_2D = ZeroPaddedBuffer2_all.reshape(L_in, Aline * Bline, order="F")

    x1 = Wavelengths_l
    x2 = Wavelengths_r
    x_new = InterpolatedWavelengths2

    f1 = interpolate.interp1d(
        x1, Z1_2D, kind="linear", axis=0, fill_value="extrapolate", assume_sorted=True
    )
    f2 = interpolate.interp1d(
        x2, Z2_2D, kind="linear", axis=0, fill_value="extrapolate", assume_sorted=True
    )
    Interp1_2D = f1(x_new)
    Interp2_2D = f2(x_new)

    InterpolatedBuffer1_all = np.reshape(Interp1_2D, (newLen, Aline, Bline), order="F")
    InterpolatedBuffer2_all = np.reshape(Interp2_2D, (newLen, Aline, Bline), order="F")

    med1 = np.median(InterpolatedBuffer1_all, axis=1, keepdims=True)
    med2 = np.median(InterpolatedBuffer2_all, axis=1, keepdims=True)
    InterpolatedBuffer1_all -= med1
    InterpolatedBuffer2_all -= med2

    # phase correction (interpolate dispersion to newLen if needed)
    def make_phase_correction(phaseDispersion, target_len, Aline):
        if phaseDispersion.size == target_len:
            ph = phaseDispersion
        else:
            idx_old = np.linspace(0.0, 1.0, phaseDispersion.size)
            idx_new = np.linspace(0.0, 1.0, target_len)
            ph = np.interp(idx_new, idx_old, phaseDispersion)
        phase_correction = np.exp(-1j * ph)
        return np.tile(phase_correction.reshape(target_len, 1), (1, Aline))

    phaseCorrection1 = make_phase_correction(phaseDispersion1, newLen, Aline)
    phaseCorrection2 = make_phase_correction(phaseDispersion2, newLen, Aline)

    InterpolatedBuffer1_all = np.multiply(
        InterpolatedBuffer1_all, phaseCorrection1[..., None], casting="unsafe"
    )
    InterpolatedBuffer2_all = np.multiply(
        InterpolatedBuffer2_all, phaseCorrection2[..., None], casting="unsafe"
    )

    InterpBuf1_big = InterpolatedBuffer1_all.reshape((newLen, Aline * Bline), order="F")
    InterpBuf2_big = InterpolatedBuffer2_all.reshape((newLen, Aline * Bline), order="F")

    # Single call per channel
    Jones1_big = Buffer2Jones(
        InterpBuf1_big, PaddingFactor, AutoCorrPeakCut
    )  # Depth x (Aline*Bline)
    Jones2_big = Buffer2Jones(InterpBuf2_big, PaddingFactor, AutoCorrPeakCut)

    # Depth is rows in Jones_big
    Depth = Jones1_big.shape[0]

    # Reshape back to (Depth, Aline, Bline) using column-major ordering so grouping matches original loop
    Jones1_depth_first = Jones1_big.reshape((Depth, Aline, Bline), order="F")
    Jones2_depth_first = Jones2_big.reshape((Depth, Aline, Bline), order="F")

    # Permute to original Jones1_3D / Jones2_3D layout (Bline x Aline x Depth)
    Jones1_3D = np.transpose(Jones1_depth_first, (2, 1, 0))
    Jones2_3D = np.transpose(Jones2_depth_first, (2, 1, 0))

    # permute to Aline x Bline x Depth
    Jones1_3D = np.transpose(Jones1_3D, (1, 0, 2))
    Jones2_3D = np.transpose(Jones2_3D, (1, 0, 2))

    # split real & imag, concat
    J1_real = np.real(Jones1_3D)
    J1_imag = -np.imag(Jones1_3D)

    J1_split = np.concatenate((J1_real, J1_imag), axis=0)

    J2_real = np.real(Jones2_3D)
    J2_imag = -np.imag(Jones2_3D)
    J2_split = np.concatenate((J2_real, J2_imag), axis=0)

    Jstack_all = np.concatenate((J1_split, J2_split), axis=0)

    # flip depth axis (MATLAB flip along 3rd dim)
    Jstack_all = np.flip(Jstack_all, axis=2)

    elapsed = time.time() - t0
    print(f"s2c_v done. elapsed: {elapsed:.1f}s. Output shape: {Jstack_all.shape}")

    return Jstack_all


# -----------------------
# === Minimal CLI ===
# -----------------------
def parse_args():
    import argparse

    p = argparse.ArgumentParser(
        description="s2c_v CLI (expects pre-reshaped spectral array)."
    )
    p.add_argument(
        "input_file",
        help="Input binary spectral filename (352-byte header expected). Use read_spectral_file_raw then reshape externally.",
    )
    p.add_argument(
        "--reshape",
        nargs=4,
        type=int,
        metavar=("AlineLength", "Aline", "channels", "Bline"),
        help="If provided, the CLI will reshape the raw uint16 to this shape (must match file layout). Example: --reshape 2048 64 2 128",
    )
    p.add_argument(
        "--disp_comp",
        default=None,
        help=(
            "Optional dispersion correction binary float64 file; the first half "
            "applies to channel 1 and the second half to channel 2."
        ),
    )
    p.add_argument(
        "--save", action="store_true", help="If set, save output NIfTI to --output"
    )
    p.add_argument("--output", default=".", help="Output path or filename (if --save).")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raw = read_spectral_file_raw(args.input_file)
    if args.reshape:
        AlineLength_r, Aline_r, channels_r, Bline_r = args.reshape
        if channels_r != 2:
            raise SystemExit("channels must be 2 when reshaping.")
        # reshape using MATLAB column-major mapping
        arr = np.reshape(raw, (AlineLength_r, Aline_r, channels_r, Bline_r), order="F")
    else:
        raise SystemExit(
            "CLI requires --reshape to be provided (this tool expects pre-reshaped input)."
        )

    Jstack = spectral2complex(
        arr, disp_comp_file=args.disp_comp, AlineLength=AlineLength_r
    )
    print("Jstack_all shape:", Jstack.shape)
    if args.save:
        base_name = os.path.splitext(os.path.basename(args.input_file))[0]
        new_base = re.sub(r"spectral.*$", "processed_cropped", base_name)
        out_file = (
            args.output
            if os.path.splitext(args.output)[1]
            else os.path.join(args.output, new_base + ".nii")
        )
        save_jstack_nifti(Jstack, out_file)
        print("Saved:", out_file)
