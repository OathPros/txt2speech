from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Kokoro Voiceover Studio"
VALID_GPU_MODES = ("Auto", "CPU", "GPU")


def app_data_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / APP_NAME
    return Path.home() / "AppData" / "Local" / APP_NAME


@dataclass
class AppSettings:
    model_directory: Path
    gpu_mode: str = "Auto"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "settings.json"

    def load(self, default_model_directory: Path) -> AppSettings:
        if not self.path.exists():
            return AppSettings(default_model_directory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read studio settings: {error}") from error
        if not isinstance(data, dict):
            raise RuntimeError("Studio settings file must contain a JSON object.")
        directory = data.get("model_directory", str(default_model_directory))
        mode = data.get("gpu_mode", "Auto")
        if not isinstance(directory, str) or not directory.strip():
            raise RuntimeError("Model storage location in settings is invalid.")
        if mode not in VALID_GPU_MODES:
            raise RuntimeError("GPU mode in settings is invalid.")
        return AppSettings(Path(directory), mode)

    def save(self, settings: AppSettings) -> None:
        if settings.gpu_mode not in VALID_GPU_MODES:
            raise ValueError(f"Unsupported GPU mode: {settings.gpu_mode}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        content = {
            "model_directory": str(settings.model_directory),
            "gpu_mode": settings.gpu_mode,
        }
        temporary.write_text(
            json.dumps(content, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
