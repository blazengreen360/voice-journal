# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPEC).resolve().parent
sys.path.insert(0, str(project_root))

asset_datas = collect_data_files("voicejournal.assets")

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=asset_datas,
    hiddenimports=[
        "onnxruntime.capi._pybind_state",
        "onnxruntime.capi.onnxruntime_pybind11_state",
        "soxr",
        "soundfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "torchaudio", "torchvision"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoiceJournal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoiceJournal",
)
app = BUNDLE(
    coll,
    name="VoiceJournal.app",
    icon=None,
    bundle_identifier="com.voicejournal.app",
    info_plist={
        "NSMicrophoneUsageDescription": "VoiceJournal uses the microphone to record spoken journal sessions.",
    },
)
