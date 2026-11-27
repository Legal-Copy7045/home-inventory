# Changelog

All notable changes to Home Inventory (the App and the companion integration), newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).
Each section is the text of its GitHub release. Versions up to 0.43.1 are described in
[home_inventory/CHANGELOG.md](home_inventory/CHANGELOG.md) and `docs/release-notes/`.

## [0.44.0] - 2026-11-27

No change to how the App or the integration works: how the project is built, checked and released now follows the owner's
project standard 1.1.0 (`.github/standard.yaml`).

### Changed
- One CI workflow (`ci.yml`) with `ci-result` as the only required check. Drafts run nothing; a ready pull request
  runs the quick checks, the privacy guard and gitleaks over its commits, and each slow job only when its files changed.
  Weekly full run on Sunday, CodeQL on Sunday through the standard's reusable workflow.
- Releases: a merged version bump creates tag `v<version>` and the release "Home Inventory <version>" with this
  changelog's section as its text, then builds and pushes the App images from that tag (`images.yml`). Earlier
  releases were tagged without the `v`. The images keep their `<version>` tags.
- One `VERSION` file; `check_versions.py` checks every place in `.github/versions.yaml`, this changelog and the App's
  changelog.
- Dependabot weekly on Sunday, grouped minor and patch updates that merge themselves once CI passes; majors wait.
- The Trivy image scan now fails on fixable HIGH vulnerabilities as well as CRITICAL ones.

### Security
- The App image upgrades the base image's setuptools (CVE-2025-47273) and leaves out its unused `tempio` tool,
  which was built with a Go version that has known HIGH vulnerabilities.
- One finding the base image's Python build reports (CVE-2025-47273, setuptools 70.3.0, not used by the App) is
  accepted in `.trivyignore`.
- The privacy guard is the standard's (history lines, photo metadata without Pillow) plus this repository's scan of
  binary and large files; the private terms are checked on `main`, from the `privacy` environment.
- Regression runner: answers the standard's commands (`check`, `brief`, `report`, `log`, `summary`, `manual`, `schema`,
  `walkthrough-due`) and writes the standard's files in `regression/out/`; new `profile.yaml` and `claims.yaml`; the
  catalogue, manual checklist and logs use the standard's formats. Check IDs are unchanged.

### Removed
- The project's own `/regression`, `/walkthrough` and `/full-check` skills (the owner's global skills replace them),
  `regression/report.py`, the separate privacy and release workflows.
