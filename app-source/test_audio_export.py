import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from audio_export import export_audio_file


class AudioExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="text2speech-test-")
        self.addCleanup(self.directory.cleanup)
        self.samples = np.sin(
            np.arange(24_000, dtype=np.float32) * 0.03
        ).astype(np.float32)

    def test_wav_is_pcm16_and_keeps_sample_rate(self):
        target = Path(self.directory.name) / "preview.wav"
        export_audio_file(target, self.samples, 24_000)
        info = sf.info(target)
        self.assertEqual(info.samplerate, 24_000)
        self.assertEqual(info.subtype, "PCM_16")

    @unittest.skipUnless(os.name == "nt", "Windows Media Foundation is Windows-only")
    def test_mp3_and_m4a_are_encoded_by_windows_media_foundation(self):
        directory = Path(self.directory.name)
        for extension, signature in ((".mp3", b"ID3"), (".m4a", b"ftyp")):
            with self.subTest(extension=extension):
                target = directory / f"preview{extension}"
                export_audio_file(target, self.samples, 24_000)
                self.assertGreater(target.stat().st_size, 100)
                header = target.read_bytes()[:16]
                self.assertIn(signature, header)

    def test_invalid_audio_and_extensions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-empty mono"):
            export_audio_file(
                Path(self.directory.name) / "empty.wav",
                np.array([], dtype=np.float32),
                24_000,
            )
        with self.assertRaisesRegex(ValueError, "WAV, MP3, or M4A"):
            export_audio_file(
                Path(self.directory.name) / "preview.ogg",
                self.samples,
                24_000,
            )


if __name__ == "__main__":
    unittest.main()
