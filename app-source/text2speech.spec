# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sysconfig

from PyInstaller.utils.hooks import collect_all
import PyInstaller.building.build_main as build_main


root = Path(SPECPATH)
onnx_capi = Path(sysconfig.get_paths()["purelib"]) / "onnxruntime" / "capi"
binaries = [
    (str(onnx_capi / "onnxruntime.dll"), "onnxruntime/capi"),
    (str(onnx_capi / "onnxruntime_providers_shared.dll"), "onnxruntime/capi"),
]
datas = []
hiddenimports = []
for package in (
    "kokoro_onnx",
    "phonemizer",
    "espeakng_loader",
    "soundfile",
    "ttkbootstrap",
    "winrt",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

find_binary_dependencies = build_main.find_binary_dependencies


def _find_binary_dependencies_without_onnxruntime_import(
    binaries, import_packages, symlink_suppression_patterns
):
    packages = [
        package
        for package in import_packages
        if not package.startswith("onnxruntime")
        and package != "kokoro_onnx"
    ]
    return find_binary_dependencies(
        binaries, packages, symlink_suppression_patterns
    )


build_main.find_binary_dependencies = _find_binary_dependencies_without_onnxruntime_import

a = build_main.Analysis(
    [str(root / "studio.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(root / "build_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = build_main.PYZ(a.pure)
exe = build_main.EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KokoroVoiceoverStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
build_main.COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="KokoroVoiceoverStudio",
)
