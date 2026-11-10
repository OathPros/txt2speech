from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


class AudioExportError(RuntimeError):
    pass


async def _transcode_with_windows_media_foundation(
    source: Path,
    destination: Path,
    extension: str,
) -> None:
    from winrt.windows.media.mediaproperties import (
        AudioEncodingQuality,
        MediaEncodingProfile,
    )
    from winrt.windows.media.transcoding import MediaTranscoder
    from winrt.windows.storage import StorageFile

    source_file = await StorageFile.get_file_from_path_async(str(source))
    destination_file = await StorageFile.get_file_from_path_async(str(destination))
    profile_factory = {
        ".mp3": MediaEncodingProfile.create_mp3,
        ".m4a": MediaEncodingProfile.create_m4a,
    }[extension]
    profile = profile_factory(AudioEncodingQuality.MEDIUM)
    result = await MediaTranscoder().prepare_file_transcode_async(
        source_file, destination_file, profile
    )
    if not result.can_transcode:
        raise AudioExportError(
            f"Windows cannot encode this audio as {extension[1:].upper()}: "
            f"{result.failure_reason}"
        )
    await result.transcode_async()


def export_audio_file(
    filename: str | Path,
    audio: np.ndarray,
    sample_rate: int,
) -> Path:
    target = Path(filename)
    extension = target.suffix.lower()
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim != 1 or samples.size == 0:
        raise ValueError("Audio must be a non-empty mono waveform.")
    if sample_rate <= 0:
        raise ValueError("Sample rate must be a positive number.")
    if extension not in {".wav", ".mp3", ".m4a"}:
        raise ValueError("Choose a WAV, MP3, or M4A file.")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="text2speech-export-", dir=target.parent
    ) as temporary_directory:
        temporary_dir = Path(temporary_directory)
        temporary_target = temporary_dir / ("audio" + extension)
        if extension == ".wav":
            sf.write(
                temporary_target,
                samples,
                sample_rate,
                format="WAV",
                subtype="PCM_16",
            )
        else:
            source = temporary_dir / "source.wav"
            sf.write(source, samples, sample_rate, format="WAV", subtype="PCM_16")
            temporary_target.touch()
            try:
                asyncio.run(
                    _transcode_with_windows_media_foundation(
                        source, temporary_target, extension
                    )
                )
            except ImportError as error:
                raise AudioExportError(
                    "Windows media encoding support is missing. Reinstall Text2Speech."
                ) from error
            except OSError as error:
                raise AudioExportError(
                    f"Windows media encoding failed: {error}"
                ) from error
        if not temporary_target.is_file() or temporary_target.stat().st_size == 0:
            raise AudioExportError("The audio encoder produced an empty file.")
        os.replace(temporary_target, target)
    return target
