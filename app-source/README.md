# Text2Speech

Text2Speech Studio 2.0 is a per-user Windows desktop app for writing scripts,
checking facts against web sources, shaping narration for speech, and exporting
audio as WAV, MP3, or M4A. Kokoro speech generation works without Qwen; AI
script preparation runs locally.

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
`dist\installer\Text2Speech-Setup-2.0.0.exe`. It installs per-user,
does not require administrator rights, preserves the existing application ID,
and does not replace the 1.0.0 installer. The app includes Kokoro assets and
the pinned CPU/CUDA llama.cpp runtimes.

The Qwen GGUF is about 5.78 GB. If it is not already present, the AI Script
Studio downloads it automatically on the first AI request, displays progress,
resumes interrupted downloads, and verifies its pinned SHA-256 before use.
Internet is needed for this initial model download. Once installed, inference
and Kokoro speech generation use localhost and work offline. The model location
can be changed in the app settings. Web searches are a separate opt-in action
and send only the research query to Bing.

When the complete, verified Qwen GGUF is available at
`release-assets\models\Qwen3.5-9B-Q4_K_M.gguf`, `package.ps1` also builds an
offline installer and ZIP at
`dist\KokoroVoiceoverStudio-Offline-2.0.0.zip`. That package contains the model
and does not need internet for installation or use. Extract all ZIP contents to
one folder and launch `KokoroVoiceoverStudio-Offline-2.0.0.exe` from that
folder; keep its `.bin` volumes beside it until setup finishes.

## Release and upgrade policy

Every public release must include the installer and matching checksum as a
stable GitHub Release in `OathPros/txt2speech`. That repository's independent
Pages workflow updates the version, installer size, download link, and older-
version list automatically. The standalone download page is
`https://oathpros.github.io/txt2speech/`. If a release-triggered Pages run is
blocked by environment protection, dispatch `pages.yml` from `main`. Verify
the new version and download link after deployment.

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

The **Write and research** workspace opens first and puts your prompt and a full,
editable narration side by side. Its default mode infers intent from a prompt,
topic, outline, or rough notes and creates polished spoken content—creative,
informative, persuasive, instructional, or professional. Choose a tone, audience,
and pacing; insert or edit pause markers directly in the text; listen to a
selection while editing; then
accept the script and send it straight to Kokoro. Tone and pacing suggest a
voice and speed, which you can apply or adjust. Save the finished narration as
a `.txt` file at any time. Preserve exact wording keeps every spoken word
unchanged and arranges pauses immediately without loading Qwen. The other
transformation modes use Qwen locally. Exact-word validation runs before a
script can be accepted.

For research-assisted scripts, enter a short query or paste up to three public
HTTPS URLs. Text2Speech searches the web or fetches readable page excerpts,
shows them for review, and uses only the sources you approve. Search queries go
to Bing only when you explicitly start a search. Script text is never sent to
search providers or cloud model services.

Qwen runs through the bundled llama.cpp server bound only to `127.0.0.1`.
Available model status, storage location, RAM, inference mode, verification,
and unload controls are in the writing workspace. No script content is sent to
a cloud service; web requests are made only when you choose to search or fetch
source URLs.

The installer is unsigned; Windows SmartScreen may show an unrecognized-app
warning. See [release notes](RELEASE_NOTES.md) and
[third-party notices](THIRD_PARTY_NOTICES.md) for additional information.
