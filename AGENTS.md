# AI Agent Guide: findminsdk

This project provides `findminsdk.py`, a cross-platform tool to analyze Android dependencies for `minSdk` compatibility and automatically lock Gradle scripts.

## When to Run

Use when:
- Gradle fails with `Manifest merger failed : uses-sdk:minSdkVersion X cannot be smaller than version Y declared in library ...`.
- User asks to upgrade dependencies while supporting legacy devices or POS terminals (e.g. API 21).
- Auditing Android dependency catalogs (`libs.versions.toml` or `build.gradle(.kts)`).

## Commands

- Audit single dependency:
  `python3 findminsdk.py <group:artifact> -m <minSdk>`
- Audit project directory:
  `python3 findminsdk.py <path_to_project> -m <minSdk>`
- Automatically lock versions in project files:
  `python3 findminsdk.py <path_to_project> -m <minSdk> --lock`
- Generate transitive dependency resolutionStrategy block:
  `python3 findminsdk.py <path_to_project> -m <minSdk> --constraints`
- Output structured JSON:
  `python3 findminsdk.py <path_to_project> -m <minSdk> --json`
