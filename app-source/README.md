# Text2Speech

Text2Speech is a per-user Windows desktop app for writing scripts, shaping
them for speech, and exporting narration as WAV, MP3, or M4A. Kokoro speech
generation works without Qwen; AI script preparation is optional and runs locally.

## Run from source

The existing Kokoro environment and model assets are reused:

```powershell
.\run_studio.bat
```

Run the regression and feature tests with:

```powershell
C:\KCS-Voiceover\.venv\Scripts\python.exe -m unittest -v
```

## Build

Install the pinned UI and Windows media dependencies, PyInstaller, and Inno
Setup 6 into the existing environment, then run:

```powershell
C:\KCS-Voiceover\.venv\Scripts\python.exe -m pip install pyinstaller ttkbootstrap==2.2.7 pillow==13.0.0 winrt-runtime==3.2.1 winrt-Windows.Foundation==3.2.1 winrt-Windows.Foundation.Collections==3.2.1 winrt-Windows.Media.Transcoding==3.2.1 winrt-Windows.Media.MediaProperties==3.2.1 winrt-Windows.Storage==3.2.1
.\package.ps1
```

The standard installer is written to
`dist\installer\Text2Speech-Setup-1.1.0.exe`. It installs per-user,
does not require administrator rights, preserves the existing application ID,
and does not replace the 1.0.0 installer. The app includes Kokoro assets and
the pinned CPU/CUDA llama.cpp runtimes.

The Qwen GGUF is about 5.78 GB. If it is not already present, the AI Script
Studio downloads it automatically on the first AI request, displays progress,
resumes interrupted downloads, and verifies its pinned SHA-256 before use.
Internet is needed for this initial model download only. Once installed,
inference and Kokoro speech generation use localhost and work offline. The
model location can be changed in AI Script Studio settings.

When the complete, verified Qwen GGUF is available at
`release-assets\models\Qwen3.5-9B-Q4_K_M.gguf`, `package.ps1` also builds an
offline installer and ZIP at
`dist\KokoroVoiceoverStudio-Offline-1.1.0.zip`. That package contains the model
and does not need internet for installation or use. Extract all ZIP contents to
one folder and launch `KokoroVoiceoverStudio-Offline-1.1.0.exe` from that
folder; keep its `.bin` volumes beside it until setup finishes.

## Release and upgrade policy

Publish each installer and matching `Text2Speech-Setup-<version>-SHA256.txt`
checksum as assets of a stable GitHub Release in `OathPros/txt2speech`. That
repository's release workflow updates and deploys
`https://dxluke.com/text2speech/` with the latest release and older published
versions. Verify the live page after the workflow completes; do not mark a
release complete before its new download link and version history are visible.

The installer keeps the existing AppId and per-user installation directory, so
new versions update the existing Text2Speech installation rather than creating
a versioned side-by-side app. Settings and the Qwen model are stored outside
that application directory. By default, the model lives at
`%LOCALAPPDATA%\Kokoro Voiceover Studio\models`; upgrades reuse the existing
verified model at its configured location instead of downloading a copy for
each app version. The offline installer uses that same shared model location.

## Use

The **Voiceover** tab has the script editor, voice and speed controls, preview,
and WAV, MP3, or M4A export. MP3 and M4A use Windows Media Foundation already
included with Windows; no separate encoder download is required. Pause directives such as `[pause 0.3s]`,
`0.3s`, `1.0s`, `...`, `....`, and `.....` are removed from spoken text and
assembled as PCM silence.

The **AI Script Studio** puts your source and a full, editable narration side
by side. Choose a transformation, tone, audience, and pacing; insert or edit
pause markers directly in the text; listen to a selection while editing; then
accept the script and send it straight to Kokoro. Tone and pacing suggest a
voice and speed, which you can apply or adjust. Save the finished narration as
a `.txt` file at any time. Preserve exact wording keeps every spoken word
unchanged and arranges pauses immediately without loading Qwen. The other
transformation modes use Qwen locally. Exact-word validation runs before a
script can be accepted.

For research-assisted scripts, paste up to three public HTTPS URLs. Text2Speech
fetches readable page excerpts, shows them for review, and uses only the
sources you approve. Script text is never sent to websites or cloud services.

Qwen runs through the bundled llama.cpp server bound only to `127.0.0.1`.
Available model status, storage location, RAM, inference mode, verification,
and unload controls are in AI Script Studio. No script content is sent to a
cloud service; web requests are made only when you choose to fetch source URLs.

The installer is unsigned; Windows SmartScreen may show an unrecognized-app
warning. See [release notes](RELEASE_NOTES.md) and
[third-party notices](THIRD_PARTY_NOTICES.md) for additional information.
