"""WhisperX progress reporting: local, atomic, advisory; never alters job checkpoints."""
import math
import time
from pathlib import Path

from ..models import now
from .metadata import atomic_json


class ProgressReporter:
    def __init__(self, directory, job_id, *, stage="transcribing", clock=time.monotonic):
        self.directory = Path(directory)
        self.job_id = job_id
        self.stage = stage
        self.clock = clock
        self.started = clock()
        self.last_write = self.started
        self.last_percent = -1.0
        self.started_at_utc = now()
        self.last_error = None
        self.update(0, force=True)

    def update(self, percent, *, force=False):
        """Percent relates only to ASR VAD chunks, not the entire Capture job."""
        if not isinstance(percent, (int, float)) or not math.isfinite(percent):
            return
        value = max(0.0, min(100.0, float(percent)))
        tick = self.clock()
        elapsed = max(0.0, tick - self.started)
        if (not force and value < 100 and value-self.last_percent < 1.0
                and tick-self.last_write < 6.0):
            return
        # ETA is derived from observed completed VAD chunks and is explicitly
        # approximate: chunk runtimes and model loads vary.
        remaining = (elapsed * (100.0-value)/value
                     if 10.0 <= value < 100.0 and elapsed >= 15 else None)
        payload = {"schema_version": 1, "job_id": self.job_id,
                   "stage": self.stage, "percent_phase": round(value, 1),
                   "estimated_remaining_seconds": round(remaining) if remaining is not None else None,
                   "estimate_basis": ("observed_vad_chunk_rate" if self.stage == "transcribing"
                                      else "observed_download_rate") if remaining is not None else None,
                   "started_at_utc": self.started_at_utc, "updated_at_utc": now(),
                   "elapsed_seconds": round(elapsed)}
        path = self.directory / "progress.json"
        if path.is_symlink():
            self.last_error = "progress_path_redirected"
            return
        try:
            atomic_json(path, payload)
            self.last_write = tick
            self.last_percent = value
        except OSError:
            # A monitoring failure must never invalidate a costly transcription.
            self.last_error = "progress_write_unavailable"
