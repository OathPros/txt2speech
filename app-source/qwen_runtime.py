from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from narration import NARRATION_SCHEMA, ScriptValidationError, validate_structured_script


MODEL_REPOSITORY = "SoAIHQ/Qwen3.5-9B-GGUF"
MODEL_REVISION = "5032137e231885dbd88e43b118c87b804b461a61"
MODEL_FILENAME = "Qwen3.5-9B-Q4_K_M.gguf"
MODEL_SIZE = 5_780_091_008
MODEL_SHA256 = "ef6f244d63abcba4f8d30b7cbd9369e9e798fc33cf21109889373f59af329c93"
MODEL_URL = (
    "https://huggingface.co/SoAIHQ/Qwen3.5-9B-GGUF/resolve/"
    f"{MODEL_REVISION}/{MODEL_FILENAME}"
)
LLAMA_BUILD = "b12230"
LLAMA_COMMIT = "a4d4f2fd4ec85ec6a0a9ece4870416c1b6cc24f5"
LLAMA_CPU_SHA256 = "a787fb94d48e6d93aa5809133f5f984360a5713b20d2f9ee1ccc4b22cd9cce56"
LLAMA_CUDA_SHA256 = "29180e373c153ca59e1837872561eeb800f5b60f1e6375653f8b7d3df57ab885"
LLAMA_CUDA_RUNTIME_SHA256 = "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
CONTEXT_SIZE = 4096
DOWNLOAD_BLOCK_SIZE = 4 * 1024 * 1024

ProgressCallback = Callable[[int, int], None]


class DownloadCancelled(Exception):
    pass


class InferenceCancelled(Exception):
    pass


def sha256_file(
    path: Path,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    done = 0
    with path.open("rb") as source:
        while block := source.read(DOWNLOAD_BLOCK_SIZE):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled("Model verification cancelled.")
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, size)
    return digest.hexdigest()


def verify_model(
    path: Path,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> bool:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        return False
    return sha256_file(path, progress, cancel).lower() == MODEL_SHA256


def download_model(
    destination: Path,
    cancel: threading.Event,
    progress: ProgressCallback | None = None,
    timeout: int = 120,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if verify_model(destination, progress, cancel):
            return destination
        raise ScriptValidationError(
            f"An existing model at {destination} failed size or checksum verification."
        )

    partial = destination.with_name(destination.name + ".part")
    attempt = 0
    while True:
        if cancel.is_set():
            raise DownloadCancelled("Model download cancelled.")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > MODEL_SIZE:
            partial.unlink()
            offset = 0

        headers = {"User-Agent": "KokoroVoiceoverStudio/2.0.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(MODEL_URL, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", response.getcode())
                if offset and status != 206:
                    partial.unlink(missing_ok=True)
                    raise OSError("Model host did not honor the resume request.")
                if offset:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        partial.unlink(missing_ok=True)
                        raise OSError("Model host returned an invalid resume range.")
                mode = "ab" if offset else "wb"
                done = offset
                with partial.open(mode) as target:
                    while block := response.read(DOWNLOAD_BLOCK_SIZE):
                        if cancel.is_set():
                            raise DownloadCancelled("Model download cancelled.")
                        target.write(block)
                        done += len(block)
                        if progress is not None:
                            progress(done, MODEL_SIZE)
            if done != MODEL_SIZE:
                raise OSError(f"Downloaded {done} of {MODEL_SIZE} model bytes.")
            digest = sha256_file(partial, progress, cancel).lower()
            if digest != MODEL_SHA256:
                partial.unlink(missing_ok=True)
                raise ScriptValidationError(
                    f"Model SHA-256 verification failed: expected {MODEL_SHA256}, got {digest}."
                )
            os.replace(partial, destination)
            return destination
        except DownloadCancelled:
            raise
        except ScriptValidationError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            attempt += 1
            if attempt >= 5:
                raise OSError(
                    f"Qwen download failed after {attempt} attempts: {error}"
                ) from error
            cancel.wait(min(2**attempt, 30))


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def available_ram() -> tuple[int, int]:
    if os.name == "nt":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Windows could not report available system memory.")
        return int(status.ullAvailPhys), int(status.ullTotalPhys)
    pages = os.sysconf("SC_AVPHYS_PAGES")
    page_size = os.sysconf("SC_PAGE_SIZE")
    total_pages = os.sysconf("SC_PHYS_PAGES")
    return pages * page_size, total_pages * page_size


def free_nvidia_vram_mib() -> int | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        return max(int(line.strip()) for line in result.stdout.splitlines() if line.strip())
    except ValueError:
        return None


class _ProcessLock:
    def __init__(self) -> None:
        self._file = None

    def acquire(self) -> None:
        lock_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Kokoro Voiceover Studio"
        lock_dir.mkdir(parents=True, exist_ok=True)
        path = lock_dir / "qwen-runtime.lock"
        self._file = path.open("a+b")
        self._file.seek(0)
        if not self._file.read(1):
            self._file.write(b"\0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self._file.close()
            self._file = None
            raise RuntimeError(
                "Qwen is already running in another Voiceover Studio window."
            ) from error

    def release(self) -> None:
        if self._file is None:
            return
        try:
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


class QwenRuntime:
    def __init__(
        self,
        app_dir: Path,
        model_path: Path,
        gpu_mode: str = "Auto",
        context_size: int = CONTEXT_SIZE,
    ) -> None:
        self.app_dir = app_dir
        self.model_path = model_path
        self.gpu_mode = gpu_mode
        self.context_size = context_size
        self.process: subprocess.Popen | None = None
        self.base_url: str | None = None
        self._process_lock = _ProcessLock()
        self._mutex = threading.RLock()
        self._cancel = threading.Event()
        self._last_used = 0.0
        self._idle_thread: threading.Thread | None = None
        self._verified_model_signature: tuple[int, int] | None = None

    def verify_installed_model(
        self,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> bool:
        if not self.model_path.is_file():
            return False
        stat = self.model_path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == self._verified_model_signature:
            return True
        if not verify_model(self.model_path, progress, cancel):
            return False
        self._verified_model_signature = signature
        return True

    def mark_model_verified(self) -> None:
        if self.model_path.is_file():
            stat = self.model_path.stat()
            self._verified_model_signature = (stat.st_size, stat.st_mtime_ns)

    def _runtime_directory(self, mode: str) -> Path:
        return self.app_dir / "runtime" / mode

    def _has_cuda_device(self) -> bool:
        server = self._runtime_directory("cuda") / "llama-server.exe"
        if not server.is_file() or free_nvidia_vram_mib() is None:
            return False
        result = subprocess.run(
            [str(server), "--list-devices"],
            cwd=server.parent,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return result.returncode == 0 and "CUDA" in result.stdout.upper()

    def _choose_backend(self) -> tuple[str, int, int | None]:
        gpu_memory = free_nvidia_vram_mib()
        has_cuda = gpu_memory is not None and self._has_cuda_device()
        if self.gpu_mode == "GPU" and not has_cuda:
            raise RuntimeError(
                "GPU acceleration was requested, but a compatible CUDA runtime/device was not found."
            )
        if self.gpu_mode != "CPU" and has_cuda:
            gpu_layers = min(24, max(1, (gpu_memory - 512) // 220))
            return "cuda", gpu_layers, gpu_memory
        return "cpu", 0, gpu_memory

    def _check_memory(self, backend: str, gpu_memory: int | None) -> None:
        available, total = available_ram()
        gib = 1024**3
        if total < 10 * gib:
            raise MemoryError("Qwen3.5-9B requires at least 10 GB of system memory.")
        if backend == "cuda":
            if available < gib:
                raise MemoryError(
                    "Less than 1 GB of system RAM is available. Close other apps before loading Qwen."
                )
            if gpu_memory is None or gpu_memory < 1536:
                raise MemoryError(
                    "At least 1.5 GB of free GPU memory is required for partial CUDA offload."
                )
        elif available < 6 * gib:
            raise MemoryError(
                "CPU inference needs about 6 GB of free system RAM. "
                f"Only {available / gib:.1f} GB is currently available."
            )

    def start(
        self,
        progress: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        with self._mutex:
            if self.process is not None and self.process.poll() is None:
                return
            if self.process is not None:
                self.stop()
            if cancel is not None and cancel.is_set():
                raise InferenceCancelled("Qwen startup cancelled.")
            if not self.model_path.is_file():
                raise FileNotFoundError(
                    f"Qwen model is not installed at {self.model_path}."
                )
            if not self.verify_installed_model(cancel=cancel):
                raise ScriptValidationError(
                    f"Qwen model failed size or SHA-256 verification: {self.model_path}"
                )
            backend, gpu_layers, gpu_memory = self._choose_backend()
            self._check_memory(backend, gpu_memory)
            server = self._runtime_directory(backend) / "llama-server.exe"
            if not server.is_file():
                raise FileNotFoundError(f"llama-server is missing: {server}")
            self._process_lock.acquire()
            try:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                args = [
                    str(server),
                    "--model",
                    str(self.model_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--ctx-size",
                    str(self.context_size),
                    "--n-gpu-layers",
                    str(gpu_layers),
                    "--parallel",
                    "1",
                    "--threads",
                    str(max(2, (os.cpu_count() or 4) - 2)),
                    "--no-webui",
                    "--log-disable",
                ]
                if progress is not None:
                    progress(f"Starting local Qwen server ({backend.upper()})...")
                self._cancel.clear()
                self.process = subprocess.Popen(
                    args,
                    cwd=server.parent,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.base_url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    if (cancel is not None and cancel.is_set()) or self._cancel.is_set():
                        raise InferenceCancelled("Qwen startup cancelled.")
                    if self.process.poll() is not None:
                        raise RuntimeError(
                            "llama-server exited while loading Qwen. "
                            "Check available RAM/VRAM and the local diagnostics."
                        )
                    try:
                        with urllib.request.urlopen(
                            self.base_url + "/health", timeout=2
                        ) as response:
                            if response.status == 200:
                                self._last_used = time.monotonic()
                                self._start_idle_timer()
                                return
                    except (OSError, urllib.error.URLError):
                        pass
                    if progress is not None:
                        progress("Loading Qwen3.5-9B into memory...")
                    time.sleep(0.5)
                raise TimeoutError("Qwen did not finish loading within 10 minutes.")
            except BaseException:
                self.stop()
                raise

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        cancel: threading.Event,
        timeout: int = 600,
        progress: Callable[[str], None] | None = None,
        temperature: float = 0.3,
    ):
        if not math.isfinite(temperature) or not 0 <= temperature <= 1:
            raise ValueError("Generation temperature must be between 0 and 1.")
        if self.base_url is None or self.process is None or self.process.poll() is not None:
            self.start(cancel=cancel)
        if cancel.is_set():
            raise InferenceCancelled("Qwen generation cancelled.")
        self._cancel = cancel
        prompt_chars = len(system_prompt) + len(user_prompt)
        estimated_prompt_tokens = math.ceil(prompt_chars / 3)
        output_budget = min(2048, self.context_size - estimated_prompt_tokens - 128)
        if output_budget < 512:
            raise ScriptValidationError(
                "This narration section is too large for the current model context. "
                "Reduce the selected section and try again."
            )
        payload = {
            "model": "qwen3.5-9b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "top_p": 0.9 if temperature >= 0.6 else 0.8,
            "max_tokens": output_budget,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "narration",
                    "strict": True,
                    "schema": NARRATION_SCHEMA,
                },
            },
        }
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        content_parts: list[str] = []
        response_started = time.monotonic()
        received_chars = 0
        reported_chars = 0
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for line in response:
                    if cancel.is_set():
                        raise InferenceCancelled("Qwen generation cancelled.")
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == b"[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise ScriptValidationError(
                            "Qwen returned an invalid streaming response."
                        ) from error
                    if "error" in event:
                        raise RuntimeError(f"Local Qwen inference failed: {event['error']}")
                    choices = event.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    piece = delta.get("content", "")
                    if not isinstance(piece, str) or not piece:
                        continue
                    content_parts.append(piece)
                    received_chars += len(piece)
                    if progress is not None and received_chars - reported_chars >= 64:
                        elapsed = max(time.monotonic() - response_started, 0.1)
                        estimated_tokens = max(1, received_chars // 4)
                        progress(
                            f"Qwen generated about {estimated_tokens} tokens "
                            f"({estimated_tokens / elapsed:.1f} tokens/s)."
                        )
                        reported_chars = received_chars
                if progress is not None and received_chars > reported_chars:
                    elapsed = max(time.monotonic() - response_started, 0.1)
                    estimated_tokens = max(1, received_chars // 4)
                    progress(
                        f"Qwen generated about {estimated_tokens} tokens "
                        f"({estimated_tokens / elapsed:.1f} tokens/s)."
                    )
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            if cancel.is_set():
                raise InferenceCancelled("Qwen generation cancelled.") from error
            raise RuntimeError(f"Local Qwen inference failed: {error}") from error
        if cancel.is_set():
            raise InferenceCancelled("Qwen generation cancelled.")
        try:
            structured = json.loads("".join(content_parts))
        except (TypeError, json.JSONDecodeError) as error:
            raise ScriptValidationError(
                "Qwen returned a response that was not valid structured narration."
            ) from error
        self._last_used = time.monotonic()
        return validate_structured_script(structured)

    def cancel_generation(self) -> None:
        self._cancel.set()
        self.stop()

    def _start_idle_timer(self) -> None:
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_thread = threading.Thread(target=self._idle_watch, daemon=True)
        self._idle_thread.start()

    def _idle_watch(self) -> None:
        while True:
            time.sleep(30)
            with self._mutex:
                if self.process is None or self.process.poll() is not None:
                    return
                if time.monotonic() - self._last_used >= 600:
                    self.stop()
                    return

    def stop(self) -> None:
        with self._mutex:
            process = self.process
            self.process = None
            self.base_url = None
            self._cancel.set()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._process_lock.release()

    def diagnostics(self) -> dict[str, int | bool | str]:
        available, total = available_ram()
        return {
            "model": MODEL_FILENAME,
            "model_installed": self.model_path.is_file(),
            "model_size_bytes": self.model_path.stat().st_size
            if self.model_path.is_file()
            else 0,
            "runtime_version": LLAMA_BUILD,
            "gpu_mode": self.gpu_mode,
            "server_running": self.process is not None
            and self.process.poll() is None,
            "available_ram_bytes": available,
            "total_ram_bytes": total,
            "free_gpu_mib": free_nvidia_vram_mib() or 0,
        }
