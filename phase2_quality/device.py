import torch

# Cache the resolved device at module load. Hardware doesn't change
# between calls — querying torch.backends.mps.is_available() per call is
# a syscall into Metal that costs ~0.5-2ms. Live-batch live evidence
# (2026-05-05): scorers were calling get_device() 6-10 times per photo,
# accumulating 3-11s of pure capability checks across a 741-photo run.
_DEVICE: "torch.device | None" = None


def get_device() -> torch.device:
    """Return MPS on Apple Silicon, CUDA if available, otherwise CPU.

    Cached after first call — hardware doesn't change at runtime.
    """
    global _DEVICE
    if _DEVICE is not None:
        return _DEVICE
    if torch.backends.mps.is_available():
        _DEVICE = torch.device("mps")
    elif torch.cuda.is_available():
        _DEVICE = torch.device("cuda")
    else:
        _DEVICE = torch.device("cpu")
    return _DEVICE
