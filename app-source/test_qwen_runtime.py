import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from narration import ScriptValidationError
import qwen_runtime
from qwen_runtime import QwenRuntime, sha256_file, verify_model


class RuntimeTests(unittest.TestCase):
    def test_model_verification_checks_exact_size_and_digest(self):
        payload = b"pinned model test bytes"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.gguf"
            path.write_bytes(payload)
            with patch.object(qwen_runtime, "MODEL_SIZE", len(payload)), patch.object(
                qwen_runtime, "MODEL_SHA256", hashlib.sha256(payload).hexdigest()
            ):
                self.assertTrue(verify_model(path))
                path.write_bytes(payload + b"x")
                self.assertFalse(verify_model(path))

    def test_checksum_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"hello")
            self.assertEqual(
                sha256_file(path),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )

    def test_local_response_is_validated_as_structured_narration(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                request = json.loads(self.rfile.read(length))
                self.server.request_payload = request
                content = json.dumps(
                    {
                        "segments": [
                            {"text": "Local narration.", "pause_after_ms": 400}
                        ]
                    }
                )
                body = (
                    "data: "
                    + json.dumps(
                        {"choices": [{"delta": {"content": content}}]}
                    )
                    + "\n\n"
                    + "data: [DONE]\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.progress_messages = []
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = QwenRuntime(Path("."), Path("model.gguf"))
            runtime.base_url = f"http://127.0.0.1:{server.server_port}"
            runtime.process = type("Process", (), {"poll": lambda self: None})()
            result = runtime.generate_structured(
                "system", "user", threading.Event(), timeout=5,
                progress=lambda message: server.progress_messages.append(message),
                temperature=0.68,
            )
            self.assertEqual(result[0].text, "Local narration.")
            self.assertEqual(result[0].pause_after_ms, 400)
            self.assertEqual(
                server.request_payload["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            self.assertEqual(
                server.request_payload["messages"][1]["content"], "user"
            )
            self.assertTrue(server.request_payload["stream"])
            self.assertEqual(server.request_payload["temperature"], 0.68)
            self.assertGreaterEqual(server.request_payload["max_tokens"], 512)
            self.assertLessEqual(server.request_payload["max_tokens"], 2048)
            self.assertTrue(server.progress_messages)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_invalid_local_structured_response_is_rejected(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = (
                    b'data: {"choices":[{"delta":{"content":"'
                    b'{\\"segments\\":[]}"}}]}\n\n'
                    b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.progress_messages = []
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = QwenRuntime(Path("."), Path("model.gguf"))
            runtime.base_url = f"http://127.0.0.1:{server.server_port}"
            runtime.process = type("Process", (), {"poll": lambda self: None})()
            with self.assertRaises(ScriptValidationError):
                runtime.generate_structured("system", "user", threading.Event(), timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_insufficient_cpu_memory_is_reported(self):
        runtime = QwenRuntime(Path("."), Path("model.gguf"), gpu_mode="CPU")
        with patch.object(
            qwen_runtime,
            "available_ram",
            return_value=(2 * 1024**3, 16 * 1024**3),
        ):
            with self.assertRaisesRegex(MemoryError, "6 GB"):
                runtime._check_memory("cpu", None)

    def test_request_that_exceeds_context_is_rejected_before_network_call(self):
        runtime = QwenRuntime(Path("."), Path("model.gguf"), context_size=1024)
        runtime.base_url = "http://127.0.0.1:1"
        runtime.process = type("Process", (), {"poll": lambda self: None})()
        with self.assertRaisesRegex(ScriptValidationError, "too large"):
            runtime.generate_structured(
                "system prompt " * 120,
                "source " * 120,
                threading.Event(),
            )

    def test_invalid_temperature_is_rejected(self):
        runtime = QwenRuntime(Path("."), Path("model.gguf"))
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            runtime.generate_structured(
                "system", "user", threading.Event(), temperature=1.5
            )

    def test_auto_gpu_offload_uses_available_memory(self):
        runtime = QwenRuntime(Path("."), Path("model.gguf"), gpu_mode="Auto")
        with patch.object(runtime, "_has_cuda_device", return_value=True), patch.object(
            qwen_runtime, "free_nvidia_vram_mib", return_value=5814
        ):
            self.assertEqual(runtime._choose_backend(), ("cuda", 24, 5814))

    def test_runtime_shutdown_terminates_server_and_releases_process_lock(self):
        class FakeProcess:
            terminated = False

            def poll(self):
                return 1 if self.terminated else None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

        class FakeLock:
            released = False

            def release(self):
                self.released = True

        runtime = QwenRuntime(Path("."), Path("model.gguf"))
        process = FakeProcess()
        lock = FakeLock()
        runtime.process = process
        runtime.base_url = "http://127.0.0.1:12345"
        runtime._process_lock = lock
        runtime.stop()
        self.assertTrue(process.terminated)
        self.assertTrue(lock.released)
        self.assertIsNone(runtime.process)
        self.assertIsNone(runtime.base_url)


if __name__ == "__main__":
    unittest.main()
