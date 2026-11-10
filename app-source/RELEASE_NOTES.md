# Release notes

## 1.1.0

- Added the AI Script Studio for locally generated narration drafts with
  Preserve exact wording, Light polish, Rewrite for narration, and Create
  script from notes modes.
- Added structured segment editing, pause controls, split/merge, selected
  passage regeneration, review/accept/reject, and direct handoff to Kokoro.
- Added resumable model download and pinned SHA-256 verification; configurable
  model location; CPU/CUDA llama.cpp runtime selection; memory diagnostics and
  model unload.
- Preserved the existing Kokoro voice, speed, preview, export, and per-user
  installation behavior.
- Updated the interface and application name to Text2Speech, and added live
  Qwen token-rate and elapsed-time progress during script generation.
- Reworked the AI workspace with a modern theme, full side-by-side script
  editing, inline pause insertion, live passage preview, and source review for
  user-provided HTTPS URLs.
- Added tone/pacing-based voice and speed suggestions, one-click TXT saving,
  and WAV/MP3/M4A export through Windows Media Foundation.
- Removed known generation-process artifacts from rewritten source material
  and tightened context budgeting to reject oversized sections before inference.
- Made Preserve exact wording immediate and model-free while keeping its exact
  word-order validation and explicit pause timing.
- Expanded regression tests for narration structure, pauses, local inference
  protocol validation, model checksum handling, and settings.

The Qwen model is downloaded on first AI use when it is not already installed.
The offline ZIP is emitted only when the exact pinned model file is available
and verifies successfully. A release must not be described as fully validated
until real local Qwen inference and installed-build Kokoro WAV export have both
been exercised.
