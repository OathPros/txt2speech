import json
import threading
import unittest

from narration import NarrationSegment, segments_to_text
from script_service import (
    CREATE_FROM_NOTES,
    LIGHT_POLISH,
    PRESERVE_WORDING,
    REWRITE_NARRATION,
    generate_narration,
)


class EchoClient:
    def __init__(self, transform=None):
        self.prompts = []
        self.temperatures = []
        self.transform = transform or (lambda text: text)

    def generate_structured(
        self,
        system_prompt,
        user_prompt,
        cancel,
        timeout=600,
        progress=None,
        temperature=0.3,
    ):
        self.prompts.append((system_prompt, user_prompt))
        self.temperatures.append(temperature)
        source = json.loads(user_prompt.split("SOURCE_JSON:\n", 1)[1])
        if progress is not None:
            progress("Qwen generated about 20 tokens.")
        return [NarrationSegment(self.transform(source), 200)]


class ScriptServiceTests(unittest.TestCase):
    def test_exact_mode_validates_words_and_preserves_explicit_pause(self):
        result = generate_narration(
            "First phrase[pause 0.3s]Second phrase...",
            None,
            mode=PRESERVE_WORDING,
        )
        self.assertEqual([s.text for s in result], ["First phrase", "Second phrase"])
        self.assertEqual([s.pause_after_ms for s in result], [300, 3000])
        self.assertTrue(result[0].explicit_pause)

    def test_exact_mode_structures_natural_pauses_without_loading_qwen(self):
        source = "First sentence. A short clause, then the end."
        result = generate_narration(source, None, mode=PRESERVE_WORDING)
        self.assertEqual(segments_to_text(result), source)
        self.assertEqual([segment.pause_after_ms for segment in result], [450, 200, 0])

    def test_all_transform_modes_produce_structured_segments(self):
        for mode in (LIGHT_POLISH, REWRITE_NARRATION, CREATE_FROM_NOTES):
            with self.subTest(mode=mode):
                client = EchoClient(lambda _text: "A spoken narration.")
                result = generate_narration("rough source notes", client, mode=mode)
                self.assertEqual(result[0].text, "A spoken narration.")

    def test_long_content_is_chunked_in_order_with_style_context(self):
        source = "A stable sentence with terminology. " * 300
        client = EchoClient()
        progress = []
        result = generate_narration(
            source, client, mode=LIGHT_POLISH, max_chunk_chars=400,
            progress=progress.append,
        )
        self.assertGreater(len(client.prompts), 2)
        self.assertEqual("".join(segment.text for segment in result), source)
        self.assertTrue(all("terminology" in prompt for prompt, _ in client.prompts))
        self.assertNotIn("this is chunk", client.prompts[0][0].lower())
        self.assertIn("Section 1 of", progress[1])
        self.assertTrue(any("Completed narration section" in message for message in progress))

    def test_custom_audience_tone_pacing_and_instructions_are_used(self):
        client = EchoClient()
        generate_narration(
            "Narration.",
            client,
            mode=REWRITE_NARRATION,
            tone="Custom tone",
            custom_tone="Reassuring and concise",
            audience="New employees",
            pacing="Deliberate",
            custom_instructions="Keep the technical terms.",
        )
        prompt = client.prompts[0][0]
        self.assertIn("Reassuring and concise", prompt)
        self.assertIn("New employees", prompt)
        self.assertIn("Keep the technical terms.", prompt)

    def test_rewrite_removes_chunk_and_editorial_artifacts(self):
        source = (
            "The studio creates narration. This is chunk 2 of 7; maintain the "
            "stated style and terminology consistently. The user can export audio."
        )
        client = EchoClient()
        result = generate_narration(source, client, mode=REWRITE_NARRATION)
        output = "".join(segment.text for segment in result)
        self.assertIn("The studio creates narration.", output)
        self.assertIn("The user can export audio.", output)
        self.assertNotIn("chunk 2 of 7", output.lower())
        self.assertNotIn("maintain the stated style", output.lower())

    def test_transform_modes_use_mode_specific_sampling(self):
        expected = (
            (LIGHT_POLISH, 0.48),
            (REWRITE_NARRATION, 0.68),
            (CREATE_FROM_NOTES, 0.68),
        )
        for mode, temperature in expected:
            with self.subTest(mode=mode):
                client = EchoClient()
                generate_narration("A sentence.", client, mode=mode)
                self.assertEqual(client.temperatures, [temperature])

    def test_rewritten_segments_remain_readable_when_model_omits_spaces(self):
        class SplitClient:
            def generate_structured(
                self, _system_prompt, _user_prompt, _cancel, **_kwargs
            ):
                return [
                    NarrationSegment("First sentence.", 300),
                    NarrationSegment("Second sentence.", 0),
                ]

        result = generate_narration(
            "Source text.", SplitClient(), mode=REWRITE_NARRATION
        )
        self.assertEqual(segments_to_text(result), "First sentence. Second sentence.")

    def test_cancelled_generation_does_not_call_model(self):
        cancel = threading.Event()
        cancel.set()
        client = EchoClient()
        with self.assertRaises(InterruptedError):
            generate_narration("A sentence.", client, cancel=cancel)
        self.assertEqual(client.prompts, [])


if __name__ == "__main__":
    unittest.main()
