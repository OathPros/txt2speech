import unittest

from narration import (
    NarrationSegment,
    ScriptDraft,
    ScriptValidationError,
    chunk_text,
    edit_segment,
    merge_segments,
    parse_explicit_pauses,
    serialize_segments,
    set_pause,
    split_segment,
    validate_structured_script,
    validate_word_preservation,
)


class StructuredNarrationTests(unittest.TestCase):
    def test_explicit_timing_forms_and_spoken_text(self):
        script = "One.[pause 0.1s]Two[0.3s]Three...[pause: 1.0s]Four....."
        segments = parse_explicit_pauses(script)
        self.assertEqual(
            [(s.text, s.pause_after_ms) for s in segments],
            [
                ("One.", 100),
                ("Two", 300),
                ("Three", 3000),
                ("", 1000),
                ("Four", 5000),
            ],
        )
        self.assertEqual("".join(s.text for s in segments), "One.TwoThreeFour")

    def test_single_period_and_non_marker_dot_run_are_not_pauses(self):
        self.assertEqual(
            [(s.text, s.pause_after_ms) for s in parse_explicit_pauses("Done. Wait......")],
            [("Done. Wait......", 0)],
        )

    def test_schema_rejects_malformed_or_unsafe_pause_values(self):
        with self.assertRaises(ScriptValidationError):
            validate_structured_script({"segments": [{"text": "Hi", "pause_after_ms": True}]})
        with self.assertRaises(ScriptValidationError):
            validate_structured_script(
                {"segments": [{"text": "Hi", "pause_after_ms": -1}]}
            )
        with self.assertRaises(ScriptValidationError):
            validate_structured_script(
                {"segments": [{"text": "Hi", "pause_after_ms": 0, "marker": "spoken"}]}
            )

    def test_exact_word_order_is_enforced(self):
        source = "First, second."
        same_words = [NarrationSegment("First ", 200), NarrationSegment("second.", 0)]
        validate_word_preservation(source, same_words)
        with self.assertRaises(ScriptValidationError):
            validate_word_preservation(source, [NarrationSegment("Second, first.", 0)])
        with self.assertRaises(ScriptValidationError):
            validate_word_preservation(source, [NarrationSegment("First, new second.", 0)])

    def test_chunking_reassembles_long_input_exactly(self):
        source = ("A sentence with useful words. " * 800) + "Ending."
        chunks = chunk_text(source, 400)
        self.assertGreater(len(chunks), 2)
        self.assertEqual("".join(chunks), source)

    def test_segment_edit_split_merge_and_pause(self):
        segments = [NarrationSegment("One sentence.", 500)]
        split_segment(segments, 0, 4)
        self.assertEqual([s.text for s in segments], ["One ", "sentence."])
        set_pause(segments, 0, 250)
        edit_segment(segments, 1, "segment.")
        merge_segments(segments, 0)
        self.assertEqual(
            [(s.text, s.pause_after_ms) for s in segments],
            [("One segment.", 500)],
        )
        self.assertEqual(serialize_segments(segments), "One segment.[pause 0.5s]")

    def test_explicit_word_mode_rejects_bad_candidate_before_acceptance(self):
        draft = ScriptDraft("Keep these exact words.")
        with self.assertRaises(ScriptValidationError):
            draft.propose([NarrationSegment("Keep these words exactly.", 500)])
        self.assertIsNone(draft.candidate)

    def test_draft_reject_accept_and_restore(self):
        draft = ScriptDraft("Keep these words.")
        draft.propose([NarrationSegment("Keep these words!", 300)])
        self.assertEqual(draft.accept()[0].text, "Keep these words!")
        draft.mode = "Rewrite for narration"
        draft.reject()
        self.assertIsNone(draft.candidate)
        restored = draft.restore_original()
        self.assertEqual(restored[0].text, "Keep these words.")


if __name__ == "__main__":
    unittest.main()
