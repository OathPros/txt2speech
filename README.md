# Text2Speech download page

This repository contains the Text2Speech download page and application source
in [`app-source/`](app-source/). The page is deployed to GitHub Pages at
`https://oathpros.github.io/txt2speech/`; Windows installers are hosted as GitHub
Release assets.

## Release updates

Publishing a stable GitHub Release with a
`Text2Speech-Setup-<version>.exe` asset automatically runs the Pages workflow.
It reads all published stable releases, updates the current download link and
version details, and lists older installer versions with release notes and
checksums when present. New releases must include the installer and matching
`Text2Speech-Setup-<version>-SHA256.txt` asset. Verify the live page after the
workflow completes.

The workflow updates the download page; it does not build or publish the
installer. See `app-source/README.md` for building and release requirements.
