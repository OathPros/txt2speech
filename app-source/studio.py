from __future__ import annotations

import io
import math
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import soundfile as sf
import ttkbootstrap
from kokoro_onnx import Kokoro
from audio_export import export_audio_file
from app_settings import (
    AppSettings,
    SettingsStore,
    VALID_GPU_MODES,
    app_data_directory,
)
from narration import (
    NarrationSegment,
    ScriptDraft,
    parse_explicit_pauses,
    serialize_segments,
)
from qwen_runtime import (
    MODEL_FILENAME,
    MODEL_SHA256,
    MODEL_SIZE,
    QwenRuntime,
    download_model,
)
from script_service import (
    PACING_OPTIONS,
    TONE_OPTIONS,
    TRANSFORMATION_MODES,
    PRESERVE_WORDING,
    CREATE_FROM_PROMPT,
    generate_narration,
)
from web_sources import (
    WebSource,
    WebSearchResult,
    WebSourceError,
    fetch_web_sources,
    search_web_sources,
)


if getattr(sys, "frozen", False):
    INSTALL_DIR = Path(sys.executable).resolve().parent / "models"
else:
    INSTALL_DIR = Path(r"C:\KCS-Voiceover")
MODEL_PATH = INSTALL_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = INSTALL_DIR / "voices-v1.0.bin"
PAUSE_MARKER = re.compile(
    r"\[(?:pause\s*:?\s*)?(\d+(?:\.\d+)?)s\]|(?<!\.)\.{3,4}(?!\.)",
    re.IGNORECASE,
)
VOICE_RECOMMENDATIONS = {
    "Professional and clear": ("af_nicole", "am_michael", "af_sarah"),
    "Natural and conversational": ("af_sarah", "am_michael", "bf_emma"),
    "Friendly and approachable": ("af_bella", "af_jessica", "am_puck"),
    "Instructional and authoritative": ("am_michael", "af_nicole", "bm_george"),
    "Warm and engaging": ("af_heart", "af_bella", "bf_emma"),
    "Calm and measured": ("bf_emma", "af_nicole", "am_michael"),
    "Custom tone": ("af_sarah", "af_nicole", "am_michael"),
}
PACING_SPEEDS = {
    "Deliberate": 0.90,
    "Natural": 1.00,
    "Energetic": 1.10,
    "Custom": 1.00,
}


def recommend_voice_and_speed(
    tone: str,
    pacing: str,
    available_voices: list[str] | tuple[str, ...],
) -> tuple[str | None, float]:
    voices = VOICE_RECOMMENDATIONS.get(tone, VOICE_RECOMMENDATIONS["Custom tone"])
    voice = next((candidate for candidate in voices if candidate in available_voices), None)
    if voice is None and available_voices:
        voice = available_voices[0]
    return voice, PACING_SPEEDS.get(pacing, 1.0)


def audio_extension_for_type(file_type: str) -> str:
    normalized = file_type.lower()
    return next(
        (extension for extension in (".mp3", ".m4a", ".wav") if extension[1:] in normalized),
        ".wav",
    )


def parse_script(script: str) -> list[tuple[str, float]]:
    """Return exact text spans paired with the pause immediately following each."""
    parts: list[tuple[str, float]] = []
    cursor = 0
    for match in PAUSE_MARKER.finditer(script):
        pause = float(match.group(1)) if match.group(1) else float(len(match.group(0)))
        parts.append((script[cursor:match.start()], pause))
        cursor = match.end()
    if cursor < len(script) or not parts:
        parts.append((script[cursor:], 0.0))
    return parts


def synthesize_script(
    engine: Kokoro,
    script: str,
    voice: str,
    speed: float,
) -> tuple[np.ndarray, int]:
    return synthesize_segments(engine, parse_explicit_pauses(script), voice, speed)


def synthesize_segments(
    engine: Kokoro,
    segments: list[NarrationSegment],
    voice: str,
    speed: float,
) -> tuple[np.ndarray, int]:
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("Speaking speed must be a positive number.")

    rendered: list[tuple[np.ndarray | None, float]] = []
    sample_rate: int | None = None
    for segment in segments:
        audio: np.ndarray | None = None
        if segment.text.strip():
            generated, current_rate = engine.create(
                segment.text, voice=voice, speed=speed, lang="en-us"
            )
            if sample_rate is None:
                sample_rate = current_rate
            elif sample_rate != current_rate:
                raise RuntimeError("Kokoro returned inconsistent sample rates.")
            audio = np.asarray(generated, dtype=np.float32).reshape(-1)
        rendered.append((audio, segment.pause_after_ms / 1000))

    if sample_rate is None:
        raise ValueError("Enter narration text before generating audio.")

    output: list[np.ndarray] = []
    for audio, pause_after in rendered:
        if audio is not None and audio.size:
            output.append(audio)
        silence_samples = round(pause_after * sample_rate)
        if silence_samples:
            output.append(np.zeros(silence_samples, dtype=np.float32))
    if not output:
        raise ValueError("Kokoro did not generate any audio.")
    return np.concatenate(output), sample_rate


class VoiceoverStudio:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Text2Speech Studio")
        self.root.geometry("1280x900")
        self.root.minsize(1040, 760)
        self.engine: Kokoro | None = None
        self.audio: np.ndarray | None = None
        self.sample_rate: int | None = None
        self.audio_is_complete = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.preview_bytes: bytes | None = None
        self.available_voices: list[str] = []
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load(app_data_directory() / "models")
        runtime_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent / "release-assets"
        )
        self.ai_runtime = QwenRuntime(
            runtime_dir, self.settings.model_directory / MODEL_FILENAME, self.settings.gpu_mode
        )
        self.ai_source_text = ""
        self.ai_candidate: list[NarrationSegment] | None = None
        self.ai_approved: list[NarrationSegment] | None = None
        self.web_sources: tuple[WebSource, ...] = ()
        self.pending_web_sources: tuple[WebSource, ...] = ()
        self.pending_web_warning = ""
        self.ai_cancel: threading.Event | None = None
        self.ai_started_at: float | None = None
        self.ai_progress_message = ""
        self.ai_last_progress_at = 0.0
        self.ai_last_displayed: tuple[str, int, int] | None = None
        self._build_ui()
        self._set_busy(True)
        self.status.set(f"Loading Kokoro from {INSTALL_DIR}...")
        threading.Thread(target=self._load_engine, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._process_events)

    def _build_ui(self) -> None:
        style = ttkbootstrap.Style(theme="minty-light")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TNotebook.Tab", padding=(22, 12), font=("Segoe UI", 10, "bold"))
        style.configure("TButton", padding=(14, 9))
        style.configure("Title.TLabel", font=("Segoe UI", 28, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Hint.TLabel", foreground="#536273", font=("Segoe UI", 9))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.status = tk.StringVar(value="Starting...")
        self.voice = tk.StringVar()
        self.speed = tk.DoubleVar(value=1.0)

        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Open Script...", command=self._open_script)
        file_menu.add_command(label="Save Script...", command=self._save_script)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menu.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menu)

        header = ttk.Frame(self.root, padding=(26, 22, 26, 12))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Text2Speech Studio", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Write naturally. Check facts. Turn your words into voiceover.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=26, pady=(4, 12))
        self.voice_tab = ttk.Frame(self.notebook)
        self.ai_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.ai_tab, text="Write and research")
        self.notebook.add(self.voice_tab, text="Voiceover")

        editor_frame = ttk.LabelFrame(self.voice_tab, text="Your narration", padding=12)
        editor_frame.grid(row=0, column=0, sticky="nsew")
        self.voice_tab.columnconfigure(0, weight=1)
        self.voice_tab.rowconfigure(0, weight=1)
        self.voice_tab.rowconfigure(1, weight=0)
        self.voice_tab.rowconfigure(2, weight=0)
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        self.editor = tk.Text(
            editor_frame,
            wrap="word",
            undo=True,
            font=("Segoe UI", 12),
            padx=10,
            pady=8,
            background="#ffffff",
            foreground="#1f2937",
            insertbackground="#1f2937",
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#d1d5db",
            highlightcolor="#2563eb",
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(editor_frame, orient="vertical", command=self.editor.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.editor.configure(yscrollcommand=scrollbar.set)
        self.editor.insert(
            "1.0",
            "Type your narration here.\n\n"
            "Add [pause 0.3s] or [pause 1.0s] for exact pauses. "
            "Use ... for 3 seconds and .... for 4 seconds.\n"
            "Pause markers are replaced with actual silence in the exported audio.",
        )

        controls = ttk.LabelFrame(self.voice_tab, text="Voice and delivery", padding=12)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(5, weight=1)
        ttk.Label(controls, text="Voice").grid(row=0, column=0, sticky="w")
        self.voice_box = ttk.Combobox(
            controls, textvariable=self.voice, state="readonly", width=22
        )
        self.voice_box.grid(row=0, column=1, padx=(8, 18), sticky="w")
        ttk.Label(controls, text="Speed").grid(row=0, column=2, sticky="w")
        self.speed_spin = ttk.Spinbox(
            controls,
            from_=0.5,
            to=2.0,
            increment=0.05,
            textvariable=self.speed,
            width=7,
        )
        self.speed_spin.grid(row=0, column=3, padx=(8, 4), sticky="w")
        ttk.Label(controls, text="×").grid(row=0, column=4, sticky="w")

        buttons = ttk.Frame(self.voice_tab, padding=(0, 8, 0, 2))
        buttons.grid(row=2, column=0, sticky="ew")
        self.generate_button = ttk.Button(
            buttons, text="Generate Preview", command=self._generate
        )
        self.generate_button.pack(side="left")
        self.play_button = ttk.Button(
            buttons, text="Play Preview", command=self._play, state="disabled"
        )
        self.play_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            buttons, text="Stop", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.export_button = ttk.Button(
            buttons, text="Export audio...", command=self._export, state="disabled"
        )
        self.export_button.pack(side="right")

        ttk.Separator(self.root).grid(row=2, column=0, sticky="ew")
        ttk.Label(self.root, textvariable=self.status, padding=(22, 8)).grid(
            row=3, column=0, sticky="ew"
        )
        self._build_ai_ui()
        self.notebook.select(self.ai_tab)

    def _build_ai_ui(self) -> None:
        self.ai_tab.columnconfigure(0, weight=1)
        self.ai_tab.rowconfigure(4, weight=1)

        options = ttk.Frame(self.ai_tab)
        options.grid(row=0, column=0, sticky="ew")
        for column in (1, 3, 5):
            options.columnconfigure(column, weight=1)
        self.ai_mode = tk.StringVar(value=CREATE_FROM_PROMPT)
        self.ai_tone = tk.StringVar(value="Natural and conversational")
        self.ai_pacing = tk.StringVar(value="Natural")
        self.ai_audience = tk.StringVar(value="General audience")
        self.ai_custom_tone = tk.StringVar()
        self.ai_custom_pacing = tk.StringVar()
        ttk.Label(options, text="Transformation").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options, textvariable=self.ai_mode, values=TRANSFORMATION_MODES,
            state="readonly", width=28,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 14))
        ttk.Label(options, text="Tone").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            options, textvariable=self.ai_tone, values=TONE_OPTIONS,
            state="readonly", width=26,
        ).grid(row=0, column=3, sticky="ew", padx=(6, 14))
        ttk.Label(options, text="Pacing").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            options, textvariable=self.ai_pacing, values=PACING_OPTIONS,
            state="readonly", width=15,
        ).grid(row=0, column=5, sticky="ew", padx=(6, 0))
        self.ai_mode_hint = tk.StringVar()
        ttk.Label(
            options, textvariable=self.ai_mode_hint, style="Hint.TLabel"
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.ai_mode.trace_add("write", self._update_mode_hint)
        self._update_mode_hint()

        options2 = ttk.Frame(self.ai_tab)
        options2.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        options2.columnconfigure(1, weight=1)
        options2.columnconfigure(3, weight=1)
        options2.columnconfigure(5, weight=1)
        ttk.Label(options2, text="Audience").grid(row=0, column=0, sticky="w")
        ttk.Entry(options2, textvariable=self.ai_audience).grid(
            row=0, column=1, sticky="ew", padx=(6, 14)
        )
        ttk.Label(options2, text="Custom tone").grid(row=0, column=2, sticky="w")
        ttk.Entry(options2, textvariable=self.ai_custom_tone).grid(
            row=0, column=3, sticky="ew", padx=(6, 14)
        )
        ttk.Label(options2, text="Custom pacing").grid(row=0, column=4, sticky="w")
        ttk.Entry(options2, textvariable=self.ai_custom_pacing).grid(
            row=0, column=5, sticky="ew", padx=(6, 0)
        )

        source_links = ttk.Frame(self.ai_tab)
        source_links.grid(row=2, column=0, sticky="ew", pady=(2, 6))
        source_links.columnconfigure(1, weight=1)
        ttk.Label(source_links, text="Research query").grid(row=0, column=0, sticky="w")
        self.ai_research_query = tk.StringVar()
        ttk.Entry(source_links, textvariable=self.ai_research_query).grid(
            row=0, column=1, sticky="ew", padx=(8, 8)
        )
        self.ai_search_sources_button = ttk.Button(
            source_links, text="Search web & review", command=self._search_sources
        )
        self.ai_search_sources_button.grid(row=0, column=2, sticky="e")
        ttk.Label(source_links, text="Or paste HTTPS URLs").grid(
            row=1, column=0, sticky="nw", pady=(6, 0)
        )
        self.ai_source_urls = tk.Text(
            source_links,
            height=2,
            wrap="word",
            font=("Segoe UI", 10),
            relief="flat",
            padx=8,
            pady=5,
        )
        self.ai_source_urls.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(6, 0))
        self.ai_fetch_sources_button = ttk.Button(
            source_links, text="Fetch & review", command=self._fetch_sources
        )
        self.ai_fetch_sources_button.grid(row=1, column=2, sticky="e", pady=(6, 0))
        self.web_source_status = tk.StringVar(
            value="Optional. Nothing is searched or sent until you request it."
        )
        ttk.Label(
            source_links, textvariable=self.web_source_status, style="Subtitle.TLabel"
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))

        instructions = ttk.Frame(self.ai_tab)
        instructions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        instructions.columnconfigure(1, weight=1)
        ttk.Label(instructions, text="Custom instructions").grid(row=0, column=0, sticky="nw")
        self.ai_instructions = tk.Text(
            instructions,
            height=2,
            wrap="word",
            font=("Segoe UI", 10),
            relief="flat",
            padx=8,
            pady=6,
        )
        self.ai_instructions.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        content = ttk.Panedwindow(self.ai_tab, orient="horizontal")
        content.grid(row=4, column=0, sticky="nsew")
        source_frame = ttk.LabelFrame(content, text="Your prompt or source", padding=12)
        output_frame = ttk.LabelFrame(content, text="Generated draft · edit freely", padding=12)
        content.add(source_frame, weight=1)
        content.add(output_frame, weight=1)
        source_frame.rowconfigure(1, weight=1)
        source_frame.columnconfigure(0, weight=1)
        ttk.Label(
            source_frame,
            text="Describe what you want created, or paste a topic, outline, or rough notes.",
            style="Hint.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 7))
        self.ai_source = tk.Text(
            source_frame, wrap="word", undo=True, font=("Segoe UI", 11),
            background="#ffffff", foreground="#1f2937", insertbackground="#1f2937",
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground="#d7dee8", highlightcolor="#3498db",
            padx=10, pady=10,
        )
        self.ai_source.grid(row=1, column=0, sticky="nsew")
        source_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=self.ai_source.yview)
        source_scroll.grid(row=1, column=1, sticky="ns")
        self.ai_source.configure(yscrollcommand=source_scroll.set)
        source_tools = ttk.Frame(source_frame)
        source_tools.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(source_tools, text="Import...", command=self._import_ai_source).pack(side="left")
        ttk.Button(
            source_tools, text="Restore source", command=self._restore_original
        ).pack(side="left", padx=6)

        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.ai_output = tk.Text(
            output_frame,
            wrap="word",
            undo=True,
            font=("Segoe UI", 12),
            background="#ffffff",
            foreground="#1f2937",
            insertbackground="#1f2937",
            selectbackground="#b7d9f4",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d7dee8",
            highlightcolor="#3498db",
            padx=12,
            pady=12,
        )
        self.ai_output.grid(row=0, column=0, sticky="nsew")
        output_scroll = ttk.Scrollbar(
            output_frame, orient="vertical", command=self.ai_output.yview
        )
        output_scroll.grid(row=0, column=1, sticky="ns")
        self.ai_output.configure(yscrollcommand=output_scroll.set)
        self.ai_output.bind("<<Modified>>", self._ai_output_changed)

        output_tools = ttk.Frame(output_frame)
        output_tools.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(output_tools, text="Pause").pack(side="left")
        self.pause_insert_ms = tk.StringVar(value="300")
        ttk.Spinbox(
            output_tools,
            from_=100,
            to=300000,
            increment=100,
            textvariable=self.pause_insert_ms,
            width=8,
        ).pack(side="left", padx=(6, 4))
        ttk.Label(output_tools, text="ms").pack(side="left")
        ttk.Button(
            output_tools, text="Insert at cursor", command=self._insert_pause_marker
        ).pack(side="left", padx=(8, 12))
        self.ai_preview_button = ttk.Button(
            output_tools,
            text="Listen to selection",
            command=self._preview_selected,
            state="disabled",
        )
        self.ai_preview_button.pack(side="left")
        self.ai_stop_button = ttk.Button(
            output_tools, text="Stop", command=self._stop, state="disabled"
        )
        self.ai_stop_button.pack(side="left", padx=6)
        self.ai_regenerate_button = ttk.Button(
            output_tools,
            text="Rewrite selection",
            command=self._regenerate_selected,
            state="disabled",
        )
        self.ai_regenerate_button.pack(side="left")

        controls = ttk.Frame(self.ai_tab)
        controls.grid(row=5, column=0, sticky="ew", pady=(10, 4))
        self.ai_generate_button = ttk.Button(
            controls,
            text="Create narration",
            command=self._generate_ai,
            style="primary.TButton",
        )
        self.ai_generate_button.pack(side="left")
        self.ai_cancel_button = ttk.Button(
            controls, text="Cancel", command=self._cancel_ai, state="disabled"
        )
        self.ai_cancel_button.pack(side="left", padx=5)
        self.ai_accept_button = ttk.Button(
            controls,
            text="Accept changes",
            command=self._accept_ai,
            state="disabled",
            style="success.TButton",
        )
        self.ai_accept_button.pack(side="left")
        self.ai_reject_button = ttk.Button(
            controls, text="Reject", command=self._reject_ai, state="disabled"
        )
        self.ai_reject_button.pack(side="left", padx=5)
        self.ai_send_button = ttk.Button(
            controls,
            text="Continue to voiceover",
            command=self._send_to_kokoro,
            state="disabled",
            style="primary.TButton",
        )
        self.ai_send_button.pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Copy", command=self._copy_ai_script).pack(side="right")
        ttk.Button(
            controls, text="Save as TXT...", command=self._save_ai_script
        ).pack(
            side="right", padx=6
        )

        recommendation = ttk.Frame(self.ai_tab)
        recommendation.grid(row=6, column=0, sticky="ew", pady=(0, 6))
        recommendation.columnconfigure(0, weight=1)
        self.recommendation_text = tk.StringVar(value="Voice and speed suggestion: loading voices…")
        ttk.Label(
            recommendation, textvariable=self.recommendation_text, style="Subtitle.TLabel"
        ).pack(side="left")
        self.recommendation_button = ttk.Button(
            recommendation, text="Use recommendation", command=self._apply_recommendation,
            state="disabled",
        )
        self.recommendation_button.pack(side="right")
        self.ai_tone.trace_add("write", self._update_recommendation)
        self.ai_pacing.trace_add("write", self._update_recommendation)

        settings = ttk.LabelFrame(self.ai_tab, text="Local AI settings", padding=8)
        settings.grid(row=7, column=0, sticky="ew", pady=(2, 0))
        settings.columnconfigure(1, weight=1)
        self.model_status = tk.StringVar()
        self._refresh_model_status()
        ttk.Label(settings, textvariable=self.model_status, wraplength=900).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        self.model_path_text = tk.StringVar(value=str(self.settings.model_directory))
        ttk.Entry(settings, textvariable=self.model_path_text, state="readonly").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )
        self.model_location_button = ttk.Button(
            settings, text="Choose location...", command=self._choose_model_location
        )
        self.model_location_button.grid(row=1, column=2, padx=5, pady=(5, 0))
        self.model_verify_button = ttk.Button(
            settings, text="Verify model", command=self._verify_model_async
        )
        self.model_verify_button.grid(row=1, column=3, pady=(5, 0))
        self.model_unload_button = ttk.Button(
            settings, text="Unload Qwen", command=self._unload_ai
        )
        self.model_unload_button.grid(row=1, column=4, padx=(5, 0), pady=(5, 0))
        ttk.Label(settings, text="Inference mode").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.gpu_mode = tk.StringVar(value=self.settings.gpu_mode)
        self.gpu_box = ttk.Combobox(
            settings, textvariable=self.gpu_mode, values=VALID_GPU_MODES,
            state="readonly", width=10,
        )
        self.gpu_box.grid(row=2, column=1, sticky="w", pady=(5, 0))
        self.gpu_box.bind("<<ComboboxSelected>>", self._change_gpu_mode)
        self._update_recommendation()

    def _update_recommendation(self, *_args: object) -> None:
        voice, speed = recommend_voice_and_speed(
            self.ai_tone.get(), self.ai_pacing.get(), self.available_voices
        )
        self.recommended_voice = voice
        self.recommended_speed = speed
        if voice is None:
            self.recommendation_text.set(
                f"Suggested delivery: {speed:.2f}× · narrator list loading"
            )
            self.recommendation_button.configure(state="disabled")
            return
        self.recommendation_text.set(
            f"Suggested delivery: {voice} · {speed:.2f}×"
        )
        self.recommendation_button.configure(state="normal")

    def _update_mode_hint(self, *_args: object) -> None:
        hints = {
            CREATE_FROM_PROMPT: (
                "Turn a prompt, topic, outline, or rough notes into polished content "
                "that fits the intent."
            ),
            "Create script from notes": (
                "Turn notes into narration; creative details are welcome when the "
                "notes request fiction."
            ),
            "Rewrite for narration": (
                "Improve flow and delivery while preserving the source's meaning."
            ),
            "Light polish": "Make small clarity and speaking-rhythm improvements.",
            PRESERVE_WORDING: (
                "Keep every word exactly as written; only arrange natural pauses."
            ),
        }
        self.ai_mode_hint.set(hints.get(self.ai_mode.get(), ""))

    def _apply_recommendation(self) -> None:
        if self.recommended_voice is None:
            return
        self.voice.set(self.recommended_voice)
        self.speed.set(self.recommended_speed)
        self.status.set(
            f"Suggested delivery applied: {self.recommended_voice} at "
            f"{self.recommended_speed:.2f}×."
        )

    def _refresh_model_status(self, message: str | None = None) -> None:
        model_path = self.settings.model_directory / MODEL_FILENAME
        if message:
            self.model_status.set(message)
        elif model_path.is_file():
            size_gib = model_path.stat().st_size / (1024**3)
            diagnostics = self.ai_runtime.diagnostics()
            available_gib = diagnostics["available_ram_bytes"] / (1024**3)
            self.model_status.set(
                f"Qwen3.5-9B Q4_K_M | Installed ({size_gib:.2f} GiB) | "
                f"llama.cpp {diagnostics['runtime_version']} | "
                f"{self.settings.gpu_mode} inference | "
                f"{available_gib:.1f} GiB RAM available"
            )
        else:
            self.model_status.set(
                f"Qwen3.5-9B Q4_K_M | Not installed | Download: "
                f"{MODEL_SIZE / (1024**3):.2f} GiB"
            )

    def _set_ai_progress(self, message: str) -> None:
        now = time.monotonic()
        if self.ai_started_at is None:
            self.ai_started_at = now
        self.ai_progress_message = message
        self.ai_last_progress_at = now
        self.ai_last_displayed = None

    def _update_ai_progress(self) -> None:
        if self.ai_cancel is None or self.ai_started_at is None:
            return
        now = time.monotonic()
        elapsed = int(now - self.ai_started_at)
        idle = int(now - self.ai_last_progress_at)
        display_key = (self.ai_progress_message, elapsed, idle)
        if display_key == self.ai_last_displayed:
            return
        message = self.ai_progress_message
        if (
            "Generating script chunk" in message or message.startswith("Chunk ")
        ) and idle >= 12:
            message += f" - waiting for Qwen response ({idle}s)"
        self._refresh_model_status(f"{message} ({elapsed}s elapsed)")
        self.ai_last_displayed = display_key

    def _set_ai_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.ai_generate_button.configure(state=state)
        self.ai_cancel_button.configure(state="normal" if busy else "disabled")
        self.ai_output.configure(state="disabled" if busy else "normal")
        self.ai_fetch_sources_button.configure(state="disabled" if busy else "normal")
        self.ai_search_sources_button.configure(state="disabled" if busy else "normal")
        for control in (
            self.model_location_button,
            self.model_verify_button,
            self.model_unload_button,
        ):
            control.configure(state="disabled" if busy else "normal")
        self.gpu_box.configure(state="disabled" if busy else "readonly")

    def _fetch_sources(self) -> None:
        urls = self.ai_source_urls.get("1.0", "end-1c").strip()
        if not urls:
            messagebox.showinfo(
                "Web sources", "Paste one or more public HTTPS URLs first.", parent=self.root
            )
            return
        self.ai_fetch_sources_button.configure(state="disabled")
        self.web_source_status.set("Fetching source pages…")
        threading.Thread(
            target=self._run_source_fetch, args=(urls,), daemon=True
        ).start()

    def _search_sources(self) -> None:
        query = self.ai_research_query.get().strip()
        if not query:
            messagebox.showinfo(
                "Web research", "Enter a short factual research query first.", parent=self.root
            )
            return
        self.ai_search_sources_button.configure(state="disabled")
        self.ai_fetch_sources_button.configure(state="disabled")
        self.web_source_status.set("Searching the web and preparing source excerpts…")
        threading.Thread(
            target=self._run_web_search, args=(query,), daemon=True
        ).start()

    def _run_web_search(self, query: str) -> None:
        try:
            result = search_web_sources(query)
            warning = (
                f"{result.skipped_results} search result(s) could not be fetched."
                if result.skipped_results
                else ""
            )
            self.events.put(("sources_fetched", result))
        except WebSourceError as error:
            self.events.put(("sources_failed", str(error)))

    def _run_source_fetch(self, urls: str) -> None:
        try:
            self.events.put(("sources_fetched", fetch_web_sources(urls)))
        except WebSourceError as error:
            self.events.put(("sources_failed", str(error)))

    def _review_fetched_sources(
        self, sources: tuple[WebSource, ...], warning: str = ""
    ) -> None:
        self.pending_web_sources = sources
        self.pending_web_warning = warning
        window = tk.Toplevel(self.root)
        window.title("Review web sources")
        window.geometry("720x520")
        window.transient(self.root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        preview = tk.Text(
            window, wrap="word", font=("Segoe UI", 10), padx=14, pady=12, relief="flat"
        )
        preview.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=12, pady=12)
        if warning:
            preview.insert("end", f"Notice: {warning}\n\n")
        for source in sources:
            preview.insert("end", f"{source.title}\n{source.url}\n\n{source.text}\n\n")
        preview.configure(state="disabled")
        ttk.Button(
            window,
            text="Use these sources",
            command=lambda: self._accept_web_sources(window),
            style="primary.TButton",
        ).grid(row=1, column=0, sticky="e", padx=6, pady=(0, 12))
        ttk.Button(
            window, text="Discard", command=window.destroy
        ).grid(row=1, column=1, sticky="w", padx=6, pady=(0, 12))

    def _accept_web_sources(self, window: tk.Toplevel) -> None:
        self.web_sources = self.pending_web_sources
        count = len(self.web_sources)
        self.web_source_status.set(
            f"{count} source{'s' if count != 1 else ''} reviewed"
            + (f" · {self.pending_web_warning}" if self.pending_web_warning else "")
        )
        window.destroy()

    def _import_ai_source(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            text = Path(filename).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            messagebox.showerror("Could not import text", str(error), parent=self.root)
            return
        self.ai_source.delete("1.0", "end")
        self.ai_source.insert("1.0", text)
        self.ai_source_text = text
        self.ai_candidate = None
        self.ai_approved = None
        self._populate_segments(None)

    def _generate_ai(self) -> None:
        source = self.ai_source.get("1.0", "end-1c")
        if not source.strip():
            messagebox.showerror(
                "AI Script Studio", "Enter or import source text first.", parent=self.root
            )
            return
        self.ai_source_text = source
        self.ai_approved = None
        self._populate_segments(None)
        self.ai_cancel = threading.Event()
        self._set_ai_progress("Preparing your narration…")
        self._set_ai_busy(True)
        self.ai_accept_button.configure(state="disabled")
        self.ai_reject_button.configure(state="disabled")
        self.ai_send_button.configure(state="disabled")
        self._update_ai_progress()
        options = self._generation_options()
        threading.Thread(
            target=self._run_ai_generation,
            args=(source, options, self.ai_cancel),
            daemon=True,
        ).start()

    def _generation_options(self) -> dict[str, object]:
        return {
            "mode": self.ai_mode.get(),
            "tone": self.ai_tone.get(),
            "custom_tone": self.ai_custom_tone.get().strip(),
            "pacing": self.ai_pacing.get(),
            "custom_pacing": self.ai_custom_pacing.get().strip(),
            "audience": self.ai_audience.get().strip(),
            "custom_instructions": self.ai_instructions.get("1.0", "end-1c").strip(),
            "web_sources": self.web_sources,
        }

    def _run_ai_generation(
        self, source: str, options: dict[str, object], cancel: threading.Event
    ) -> None:
        try:
            client = (
                None
                if options["mode"] == PRESERVE_WORDING
                else self._ensure_ai_runtime(cancel)
            )
            segments = generate_narration(
                source,
                client,
                **options,
                cancel=cancel,
                progress=lambda text: self.events.put(("ai_progress", text)),
            )
            self.events.put(("ai_generated", segments))
        except Exception as error:
            self.events.put(("ai_failed", str(error)))
        finally:
            self.events.put(("ai_finished", None))

    def _ensure_ai_runtime(self, cancel: threading.Event) -> QwenRuntime:
        model_path = self.settings.model_directory / MODEL_FILENAME
        self.settings.model_directory.mkdir(parents=True, exist_ok=True)
        if not model_path.exists():
            free_bytes = shutil.disk_usage(self.settings.model_directory).free
            if free_bytes < MODEL_SIZE + 1024**3:
                raise OSError(
                    f"Qwen needs {MODEL_SIZE / (1024**3):.2f} GiB plus 1 GiB "
                    f"free space; only {free_bytes / (1024**3):.2f} GiB is available."
                )

            def download_progress(done: int, total: int) -> None:
                percent = 100 * done / max(total, 1)
                self.events.put(
                    ("ai_progress", f"Downloading Qwen model: {percent:.1f}%")
                )

            self.events.put(("ai_progress", "Installing local Qwen model…"))
            download_model(model_path, cancel, download_progress)
            self.ai_runtime.mark_model_verified()
        else:
            self.events.put(("ai_progress", "Checking local Qwen model…"))
            if not self.ai_runtime.verify_installed_model(cancel=cancel):
                raise RuntimeError(
                    f"The installed model failed SHA-256 verification. "
                    f"Expected {MODEL_SHA256}."
                )
        self.ai_runtime.start(
            progress=lambda text: self.events.put(("ai_progress", text)),
            cancel=cancel,
        )
        return self.ai_runtime

    def _cancel_ai(self) -> None:
        if self.ai_cancel is not None:
            self.ai_cancel.set()
        self._set_ai_progress("Cancelling Qwen operation...")
        self._update_ai_progress()
        threading.Thread(target=self.ai_runtime.cancel_generation, daemon=True).start()

    def _populate_segments(self, segments: list[NarrationSegment] | None) -> None:
        self.ai_output.configure(state="normal")
        self.ai_output.delete("1.0", "end")
        self.ai_candidate = (
            [
                NarrationSegment(item.text, item.pause_after_ms, item.explicit_pause)
                for item in segments
            ]
            if segments is not None
            else None
        )
        if self.ai_candidate is not None:
            self.ai_output.insert("1.0", serialize_segments(self.ai_candidate))
        self.ai_output.edit_modified(False)
        has_candidate = bool(self.ai_candidate)
        self.ai_accept_button.configure(state="normal" if has_candidate else "disabled")
        self.ai_reject_button.configure(state="normal" if has_candidate else "disabled")
        self.ai_preview_button.configure(
            state="normal"
            if has_candidate and self.engine is not None
            else "disabled"
        )
        self.ai_regenerate_button.configure(
            state="normal" if has_candidate else "disabled"
        )

    def _ai_output_changed(self, _event: object = None) -> None:
        if not self.ai_output.edit_modified():
            return
        self.ai_output.edit_modified(False)
        self.ai_approved = None
        self.ai_send_button.configure(state="disabled")
        try:
            self.ai_candidate = parse_explicit_pauses(
                self.ai_output.get("1.0", "end-1c")
            )
        except ValueError as error:
            self.ai_candidate = None
            self.status.set(f"Pause marker error: {error}")
        has_text = bool(self.ai_output.get("1.0", "end-1c").strip())
        can_preview = has_text and self.engine is not None and self.ai_candidate is not None
        self.ai_accept_button.configure(
            state="normal" if self.ai_candidate is not None else "disabled"
        )
        self.ai_reject_button.configure(
            state="normal" if self.ai_candidate is not None else "disabled"
        )
        self.ai_preview_button.configure(state="normal" if can_preview else "disabled")
        self.ai_regenerate_button.configure(state="normal" if can_preview else "disabled")

    def _insert_pause_marker(self) -> None:
        try:
            milliseconds = int(self.pause_insert_ms.get())
            if milliseconds <= 0 or milliseconds > 300_000:
                raise ValueError("Pause must be between 1 and 300,000 milliseconds.")
        except (ValueError, tk.TclError) as error:
            messagebox.showerror("Invalid pause", str(error), parent=self.root)
            return
        seconds = f"{milliseconds / 1000:g}"
        self.ai_output.insert("insert", f"[pause {seconds}s]")
        self.ai_output.focus_set()

    def _regenerate_selected(self) -> None:
        try:
            start = self.ai_output.index("sel.first")
            end = self.ai_output.index("sel.last")
            source = self.ai_output.get(start, end)
        except tk.TclError:
            messagebox.showinfo(
                "Rewrite selection",
                "Select a passage in the narration first.",
                parent=self.root,
            )
            return
        if not source.strip():
            return
        self.ai_cancel = threading.Event()
        self._set_ai_busy(True)
        self._set_ai_progress("Rewriting the selected passage…")
        threading.Thread(
            target=self._run_ai_section,
            args=(source, self._generation_options(), start, end, self.ai_cancel),
            daemon=True,
        ).start()

    def _run_ai_section(
        self,
        source: str,
        options: dict[str, object],
        start: str,
        end: str,
        cancel: threading.Event,
    ) -> None:
        try:
            client = (
                None
                if options["mode"] == PRESERVE_WORDING
                else self._ensure_ai_runtime(cancel)
            )
            segments = generate_narration(
                source,
                client,
                **options,
                cancel=cancel,
                progress=lambda text: self.events.put(("ai_progress", text)),
            )
            self.events.put(("ai_section_generated", (start, end, segments)))
        except Exception as error:
            self.events.put(("ai_failed", str(error)))
        finally:
            self.events.put(("ai_finished", None))

    def _accept_ai(self) -> None:
        try:
            segments = parse_explicit_pauses(self.ai_output.get("1.0", "end-1c"))
            draft = ScriptDraft(self.ai_source_text, self.ai_mode.get())
            draft.propose(segments)
            self.ai_approved = draft.accept()
        except ValueError as error:
            messagebox.showerror("Script not accepted", str(error), parent=self.root)
            return
        self.ai_candidate = self.ai_approved
        self.ai_send_button.configure(state="normal")
        self._refresh_model_status("Narration approved. Continue to the voiceover workspace.")

    def _reject_ai(self) -> None:
        self.ai_candidate = None
        self.ai_approved = None
        self._populate_segments(None)
        self.ai_send_button.configure(state="disabled")
        self._refresh_model_status("Generated narration discarded.")

    def _restore_original(self) -> None:
        self.ai_source_text = self.ai_source.get("1.0", "end-1c")
        original = parse_explicit_pauses(self.ai_source_text)
        self.ai_approved = None
        self._populate_segments(original)
        self.ai_send_button.configure(state="disabled")
        self._refresh_model_status("Original text restored for review.")

    def _copy_ai_script(self) -> None:
        text = self.ai_output.get("1.0", "end-1c")
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._refresh_model_status("Narration copied to clipboard.")

    def _save_ai_script(self) -> None:
        text = self.ai_output.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo(
                "Save script", "Create or restore a script first.", parent=self.root
            )
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="narration.txt",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(text, encoding="utf-8")
            self._refresh_model_status(f"Saved {Path(filename).name}")
        except OSError as error:
            messagebox.showerror("Could not save script", str(error), parent=self.root)

    def _preview_selected(self) -> None:
        if self.engine is None:
            return
        try:
            source = self.ai_output.get("sel.first", "sel.last")
        except tk.TclError:
            source = self.ai_output.get("1.0", "end-1c")
        try:
            segments = parse_explicit_pauses(source)
            speed = float(self.speed.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(
                "Cannot preview", "Check the pause markers and speaking speed.",
                parent=self.root,
            )
            return
        if not any(segment.text.strip() for segment in segments):
            messagebox.showinfo(
                "Listen to narration",
                "Select or write some narration first.",
                parent=self.root,
            )
            return
        self.ai_preview_button.configure(state="disabled")
        self.status.set("Preparing audio preview…")
        threading.Thread(
            target=self._render_segments,
            args=(self.engine, segments, self.voice.get(), speed, "ai_preview"),
            daemon=True,
        ).start()

    def _render_segments(
        self,
        engine: Kokoro,
        segments: list[NarrationSegment],
        voice: str,
        speed: float,
        event_name: str,
    ) -> None:
        try:
            self.events.put(
                (event_name, synthesize_segments(engine, segments, voice, speed))
            )
        except Exception as error:
            self.events.put(("ai_failed", f"Audio preview failed: {error}"))

    def _send_to_kokoro(self) -> None:
        if self.ai_approved is None:
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", serialize_segments(self.ai_approved))
        self.notebook.select(self.voice_tab)
        self._refresh_model_status("Approved narration sent to Kokoro.")

    def _choose_model_location(self) -> None:
        directory = filedialog.askdirectory(
            parent=self.root,
            title="Choose Qwen model storage location",
            initialdir=str(self.settings.model_directory),
            mustexist=False,
        )
        if not directory:
            return
        self.settings = AppSettings(Path(directory), self.settings.gpu_mode)
        self.settings_store.save(self.settings)
        self.model_path_text.set(directory)
        self.ai_runtime.stop()
        self.ai_runtime = QwenRuntime(
            self.ai_runtime.app_dir,
            self.settings.model_directory / MODEL_FILENAME,
            self.settings.gpu_mode,
        )
        self._refresh_model_status()

    def _change_gpu_mode(self, _event: object = None) -> None:
        mode = self.gpu_mode.get()
        if mode not in VALID_GPU_MODES:
            return
        self.ai_runtime.stop()
        self.settings = AppSettings(self.settings.model_directory, mode)
        self.settings_store.save(self.settings)
        self.ai_runtime = QwenRuntime(
            self.ai_runtime.app_dir,
            self.settings.model_directory / MODEL_FILENAME,
            mode,
        )
        self._refresh_model_status()

    def _verify_model_async(self) -> None:
        self.model_status.set("Verifying model SHA-256...")

        def verify() -> None:
            try:
                valid = self.ai_runtime.verify_installed_model(
                    progress=lambda done, total: self.events.put(
                        ("ai_progress", f"Verifying model: {100 * done / max(total, 1):.1f}%")
                    ),
                )
                self.events.put(
                    ("ai_verified", "Model checksum verified." if valid else
                     "Model is missing or failed size/SHA-256 verification.")
                )
            except Exception as error:
                self.events.put(("ai_verified", f"Model verification failed: {error}"))

        threading.Thread(target=verify, daemon=True).start()

    def _unload_ai(self) -> None:
        threading.Thread(target=self.ai_runtime.stop, daemon=True).start()
        self._refresh_model_status("Unloading local Qwen model...")

    def _close(self) -> None:
        if self.ai_cancel is not None:
            self.ai_cancel.set()
        self.ai_runtime.stop()
        self.root.destroy()

    def _set_busy(self, busy: bool) -> None:
        unavailable = busy or self.engine is None
        self.generate_button.configure(state="disabled" if unavailable else "normal")
        if busy:
            self.play_button.configure(state="disabled")
            self.export_button.configure(state="disabled")
        elif self.audio is not None:
            self.play_button.configure(state="normal")
            self.export_button.configure(
                state="normal" if self.audio_is_complete else "disabled"
            )

    def _load_engine(self) -> None:
        try:
            if not MODEL_PATH.is_file() or not VOICES_PATH.is_file():
                raise FileNotFoundError(
                    f"Expected the installed model and voices at {INSTALL_DIR}."
                )
            engine = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
            self.events.put(("loaded", (engine, engine.get_voices())))
        except Exception as error:
            self.events.put(("error", f"Could not load Kokoro: {error}"))

    def _process_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "loaded":
                    engine, voices = value
                    self.engine = engine
                    self.available_voices = list(voices)
                    self.voice_box.configure(values=voices)
                    self.voice.set("af_sarah" if "af_sarah" in voices else voices[0])
                    self._update_recommendation()
                    self.status.set(f"Kokoro ready - {len(voices)} voices")
                    self.ai_preview_button.configure(
                        state="normal" if self.ai_candidate else "disabled"
                    )
                    self._set_busy(False)
                elif kind == "rendered":
                    self.audio, self.sample_rate = value
                    self.audio_is_complete = True
                    duration = len(self.audio) / self.sample_rate
                    self.status.set(f"Preview ready - {duration:.1f} seconds")
                    self._set_busy(False)
                elif kind == "ai_progress":
                    self._set_ai_progress(str(value))
                elif kind == "ai_generated":
                    self._populate_segments(value)
                    self.ai_approved = None
                    self._refresh_model_status(
                        "Draft ready. Read it, edit the words or pauses, then accept changes."
                    )
                elif kind == "ai_section_generated":
                    start, end, replacement = value
                    self.ai_output.configure(state="normal")
                    self.ai_output.delete(start, end)
                    self.ai_output.insert(start, serialize_segments(replacement))
                    self.ai_output.edit_modified(False)
                    self.ai_candidate = parse_explicit_pauses(
                        self.ai_output.get("1.0", "end-1c")
                    )
                    self.ai_approved = None
                    self.ai_send_button.configure(state="disabled")
                    self.ai_accept_button.configure(state="normal")
                    self.ai_reject_button.configure(state="normal")
                    self._refresh_model_status(
                        "Passage rewritten. Review it and accept when ready."
                    )
                elif kind == "ai_finished":
                    self._set_ai_busy(False)
                    self.ai_regenerate_button.configure(
                        state="normal" if self.ai_candidate is not None else "disabled"
                    )
                    self.ai_cancel = None
                    self.ai_started_at = None
                elif kind == "ai_failed":
                    self._refresh_model_status(str(value))
                    self.ai_preview_button.configure(
                        state="normal" if self.engine is not None else "disabled"
                    )
                    messagebox.showerror("AI Script Studio", str(value), parent=self.root)
                elif kind == "sources_fetched":
                    self.ai_fetch_sources_button.configure(state="normal")
                    self.ai_search_sources_button.configure(state="normal")
                    if isinstance(value, WebSearchResult):
                        warning = (
                            f"{value.skipped_results} search result(s) could not be fetched."
                            if value.skipped_results
                            else ""
                        )
                        self._review_fetched_sources(value.sources, warning)
                    else:
                        self._review_fetched_sources(value)
                elif kind == "sources_failed":
                    self.ai_fetch_sources_button.configure(state="normal")
                    self.ai_search_sources_button.configure(state="normal")
                    self.web_source_status.set("Could not fetch sources")
                    messagebox.showerror("Web sources", str(value), parent=self.root)
                elif kind == "ai_verified":
                    self._refresh_model_status(str(value))
                elif kind == "ai_preview":
                    self.audio, self.sample_rate = value
                    self.audio_is_complete = False
                    self.export_button.configure(state="disabled")
                    self.status.set(
                        f"Preview ready - "
                        f"{len(self.audio) / self.sample_rate:.1f} seconds"
                    )
                    self.ai_preview_button.configure(state="normal")
                    self._play()
                elif kind == "audio_exported":
                    self.export_button.configure(state="normal")
                    self.status.set(f"Exported {Path(value).name}")
                elif kind == "audio_export_failed":
                    self.export_button.configure(state="normal")
                    messagebox.showerror("Export failed", str(value), parent=self.root)
                elif kind == "error":
                    self.status.set(str(value))
                    self._set_busy(False)
                    messagebox.showerror("Text2Speech", str(value), parent=self.root)
                elif kind == "playback_error":
                    self.status.set(str(value))
                    self.stop_button.configure(state="disabled")
                    self.ai_stop_button.configure(state="disabled")
                    messagebox.showerror("Preview", str(value), parent=self.root)
                elif kind == "playback_done":
                    self.stop_button.configure(state="disabled")
                    self.ai_stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self._update_ai_progress()
        self.root.after(100, self._process_events)

    def _generate(self) -> None:
        if self.engine is None:
            return
        script = self.editor.get("1.0", "end-1c")
        voice = self.voice.get()
        try:
            speed = float(self.speed.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid speed", "Enter a numeric speaking speed.", parent=self.root)
            return
        self.audio = None
        self.sample_rate = None
        self.audio_is_complete = False
        self._set_busy(True)
        self.status.set("Generating narration...")
        threading.Thread(
            target=self._render, args=(self.engine, script, voice, speed), daemon=True
        ).start()

    def _render(self, engine: Kokoro, script: str, voice: str, speed: float) -> None:
        try:
            self.events.put(("rendered", synthesize_script(engine, script, voice, speed)))
        except Exception as error:
            self.events.put(("error", f"Audio generation failed: {error}"))

    def _play(self) -> None:
        if self.audio is None or self.sample_rate is None:
            return
        try:
            import winsound
        except ImportError as error:
            messagebox.showerror("Preview unavailable", str(error), parent=self.root)
            return
        buffer = io.BytesIO()
        sf.write(buffer, self.audio, self.sample_rate, format="WAV", subtype="PCM_16")
        self.preview_bytes = buffer.getvalue()
        self.stop_button.configure(state="normal")
        self.ai_stop_button.configure(state="normal")
        self.status.set("Playing preview...")
        threading.Thread(target=self._play_bytes, args=(self.preview_bytes,), daemon=True).start()

    def _play_bytes(self, data: bytes) -> None:
        import winsound

        try:
            winsound.PlaySound(data, winsound.SND_MEMORY)
            self.events.put(("playback_done", None))
        except Exception as error:
            self.events.put(("playback_error", f"Could not play preview: {error}"))

    def _stop(self) -> None:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
        self.stop_button.configure(state="disabled")
        self.ai_stop_button.configure(state="disabled")
        self.status.set("Playback stopped")

    def _export(self) -> None:
        if self.audio is None or self.sample_rate is None or not self.audio_is_complete:
            return
        selected_type = tk.StringVar()
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export narration",
            typevariable=selected_type,
            filetypes=[
                ("WAV audio", "*.wav"),
                ("MP3 audio", "*.mp3"),
                ("M4A audio", "*.m4a"),
            ],
            initialfile="voiceover",
        )
        if not filename:
            return
        target = Path(filename)
        if not target.suffix:
            target = target.with_suffix(audio_extension_for_type(selected_type.get()))
        if target.suffix.lower() not in {".wav", ".mp3", ".m4a"}:
            messagebox.showerror(
                "Unsupported format", "Choose WAV, MP3, or M4A.", parent=self.root
            )
            return
        self.export_button.configure(state="disabled")
        self.status.set(f"Exporting {target.suffix[1:].upper()} audio…")
        threading.Thread(
            target=self._run_audio_export,
            args=(target, self.audio, self.sample_rate),
            daemon=True,
        ).start()

    def _run_audio_export(
        self, target: Path, audio: np.ndarray, sample_rate: int
    ) -> None:
        try:
            self.events.put(
                ("audio_exported", export_audio_file(target, audio, sample_rate))
            )
        except Exception as error:
            self.events.put(("audio_export_failed", str(error)))

    def _open_script(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            text = Path(filename).read_text(encoding="utf-8-sig")
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", text)
            self.status.set(f"Opened {Path(filename).name}")
        except (OSError, UnicodeError) as error:
            messagebox.showerror("Could not open script", str(error), parent=self.root)

    def _save_script(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile="script.txt",
        )
        if not filename:
            return
        try:
            text = self.editor.get("1.0", "end-1c")
            Path(filename).write_text(text, encoding="utf-8")
            self.status.set(f"Saved {Path(filename).name}")
        except OSError as error:
            messagebox.showerror("Could not save script", str(error), parent=self.root)


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        if not MODEL_PATH.is_file() or not VOICES_PATH.is_file():
            raise FileNotFoundError(f"Model assets not found in {INSTALL_DIR}.")
        engine = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
        audio, sample_rate = engine.create(
            "Text2Speech is ready.",
            voice="af_sarah",
            speed=1.0,
            lang="en-us",
        )
        if not len(audio):
            raise RuntimeError("Kokoro did not generate audio.")
        print(f"OK: generated {len(audio)} samples at {sample_rate} Hz.")
        return
    root = tk.Tk()
    VoiceoverStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()
