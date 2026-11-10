# Third-party notices

The distributable contains third-party software and model files. Keep this
notice with redistributed builds and consult the referenced licenses for their
full terms.

| Component | Pinned version/source | License and notice |
|---|---|---|
| Qwen3.5-9B Q4_K_M GGUF | `SoAIHQ/Qwen3.5-9B-GGUF`, revision `5032137e231885dbd88e43b118c87b804b461a61`; upstream model revision `c202236235762e1c871ad0ccb60c8ee5ba337b9a` | Apache-2.0; see the model repository's `LICENSE` and model card |
| llama.cpp / llama-server | Release `b12230`, commit `a4d4f2fd4ec85ec6a0a9ece4870416c1b6cc24f5` | MIT; https://github.com/ggml-org/llama.cpp/blob/master/LICENSE |
| llama.cpp CPU build | Windows x64 release archive, SHA-256 `a787fb94d48e6d93aa5809133f5f984360a5713b20d2f9ee1ccc4b22cd9cce56` | Includes LLVM OpenMP runtime; `LICENSE-LLVM-OpenMP` is copied into the packaged runtime directory |
| llama.cpp CUDA 12.4 build | Windows x64 release archive, SHA-256 `29180e373c153ca59e1837872561eeb800f5b60f1e6375653f8b7d3df57ab885` | llama.cpp MIT; includes CUDA backend |
| NVIDIA CUDA 12.4 runtime libraries | Windows x64 redistributable archive, SHA-256 `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` | NVIDIA CUDA Toolkit EULA and redistributable terms; https://docs.nvidia.com/cuda/eula/index.html |
| Kokoro ONNX and voice assets | Existing locally installed v1.0 assets | Preserve the upstream Kokoro model and asset notices supplied with the source distribution |
| ttkbootstrap | 2.2.7 | MIT; https://github.com/israel-dryer/ttkbootstrap |
| Pillow | 13.0.0 | MIT-CMU; https://github.com/python-pillow/Pillow |
| PyWinRT runtime and projections | 3.2.1 (`Windows.Foundation`, `Windows.Foundation.Collections`, `Windows.Media.Transcoding`, `Windows.Media.MediaProperties`, `Windows.Storage`) | MIT; https://github.com/pywinrt/pywinrt |
| Python packages | Versions bundled by the current PyInstaller environment | See each package's license/notice files in the packaged `_internal` directory |

Model and runtime provenance, revisions, checksums, and archive details are
also recorded in `DEPENDENCIES.json`.
