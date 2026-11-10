import threading
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from narration import parse_explicit_pauses
from script_service import PRESERVE_WORDING
from studio import (
    VoiceoverStudio,
    audio_extension_for_type,
    parse_script,
    recommend_voice_and_speed,
    synthesize_script,
)


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []

    def create(self, text: str, voice: str, speed: float, lang: str):
        self.calls.append((text, voice, speed))
        return np.ones(4, dtype=np.float32), 10


class ScriptParsingTests(unittest.TestCase):
    def test_audio_export_uses_selected_file_type_for_missing_extension(self) -> None:
        self.assertEqual(audio_extension_for_type("MP3 audio (*.mp3)"), ".mp3")
        self.assertEqual(audio_extension_for_type("*.m4a"), ".m4a")
        self.assertEqual(audio_extension_for_type("WAV audio"), ".wav")

    def test_exact_text_and_pause_markers(self) -> None:
        script = "  First line[pause 0.3s]\nSecond....Third..."
        self.assertEqual(
            parse_script(script),
            [
                ("  First line", 0.3),
                ("\nSecond", 4.0),
                ("Third", 3.0),
            ],
        )

    def test_short_marker_and_unmatched_dot_runs_remain_text(self) -> None:
        self.assertEqual(
            parse_script("A[1.0s]B.....C"),
            [("A", 1.0), ("B.....C", 0.0)],
        )

    def test_synthesis_inserts_pcm_silence_and_preserves_spans(self) -> None:
        engine = FakeEngine()
        audio, rate = synthesize_script(
            engine, "Hello[pause: 0.3s] world....Done", "af_sarah", 0.95
        )
        self.assertEqual([call[0] for call in engine.calls], ["Hello", " world", "Done"])
        self.assertTrue(all(call[1:] == ("af_sarah", 0.95) for call in engine.calls))
        self.assertEqual(rate, 10)
        np.testing.assert_array_equal(
            audio,
            np.concatenate(
                [
                    np.ones(4),
                    np.zeros(3),
                    np.ones(4),
                    np.zeros(40),
                    np.ones(4),
                ]
            ),
        )

    def test_empty_script_is_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "Enter narration text"):
            synthesize_script(FakeEngine(), " ... .... ", "af_sarah", 1.0)

    def test_exact_five_second_pause_is_pcm_silence(self) -> None:
        engine = FakeEngine()
        audio, rate = synthesize_script(engine, "First.....Second", "af_sarah", 1.0)
        self.assertEqual(rate, 10)
        self.assertEqual([call[0] for call in engine.calls], ["First", "Second"])
        self.assertEqual(len(audio), 58)
        np.testing.assert_array_equal(audio[4:54], np.zeros(50, dtype=np.float32))

    def test_standalone_decimal_pause_marker_is_not_spoken(self) -> None:
        segments = parse_explicit_pauses("Start. 0.3s End and 1.0s later.")
        self.assertEqual(
            [(segment.text, segment.pause_after_ms) for segment in segments],
            [("Start. ", 300), (" End and ", 1000), (" later.", 0)],
        )

    def test_model_verification_uses_runtime_progress_callback(self) -> None:
        class FakeRuntime:
            def verify_installed_model(self, progress=None, cancel=None):
                progress(1, 2)
                return True

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        events = Queue()
        studio = SimpleNamespace(
            ai_runtime=FakeRuntime(),
            events=events,
            model_status=SimpleNamespace(set=lambda _message: None),
        )
        with patch("studio.threading.Thread", ImmediateThread):
            VoiceoverStudio._verify_model_async(studio)

        self.assertEqual(events.get_nowait(), ("ai_progress", "Verifying model: 50.0%"))
        self.assertEqual(events.get_nowait(), ("ai_verified", "Model checksum verified."))

    def test_slow_chunk_progress_shows_elapsed_wait_time(self) -> None:
        messages = []
        studio = SimpleNamespace(
            ai_cancel=object(),
            ai_started_at=100.0,
            ai_progress_message="Generating script chunk 1 of 7...",
            ai_last_progress_at=100.0,
            ai_last_displayed=None,
            _refresh_model_status=messages.append,
        )
        with patch("studio.time.monotonic", return_value=115.0):
            VoiceoverStudio._update_ai_progress(studio)

        self.assertIn("waiting for Qwen response (15s)", messages[0])
        self.assertIn("(15s elapsed)", messages[0])

    def test_tone_and_pacing_recommend_available_voice_and_speed(self) -> None:
        voice, speed = recommend_voice_and_speed(
            "Warm and engaging",
            "Deliberate",
            ["af_sarah", "af_heart", "am_michael"],
        )
        self.assertEqual(voice, "af_heart")
        self.assertEqual(speed, 0.9)

    def test_voice_recommendation_falls_back_to_available_voice(self) -> None:
        voice, speed = recommend_voice_and_speed(
            "Professional and clear", "Energetic", ["af_sarah"]
        )
        self.assertEqual(voice, "af_sarah")
        self.assertEqual(speed, 1.1)

    def test_exact_mode_generation_does_not_initialize_qwen(self) -> None:
        events = Queue()
        studio = SimpleNamespace(events=events)
        VoiceoverStudio._run_ai_generation(
            studio,
            "First sentence. Second sentence.",
            {"mode": PRESERVE_WORDING},
            threading.Event(),
        )
        messages = [events.get_nowait() for _ in range(3)]
        self.assertEqual(messages[1][0], "ai_generated")
        self.assertEqual(messages[2], ("ai_finished", None))


if __name__ == "__main__":
    unittest.main()
