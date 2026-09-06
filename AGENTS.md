# AI Agent Guide: findminsdk

This project provides `findminsdk.py`, a cross-platform tool to analyze Android dependencies for `minSdk` compatibility.

The script focuses on analysis and structured recommendations. As an AI agent, you inspect the output and update project files directly, which is safer for modular architectures and custom Gradle conventions.

## When to Run

Use when:
- Gradle fails with `Manifest merger failed : uses-sdk:minSdkVersion X cannot be smaller than version Y declared in library ...`.
- User asks to upgrade dependencies while supporting legacy devices or POS terminals (e.g. API 21).
- Auditing Android dependency catalogs (`libs.versions.toml` or `build.gradle(.kts)`).

## Commands

- Output structured JSON with file locations and version refs:
  `python3 findminsdk.py <path_to_project> -m <minSdk> --json`
- Display human-readable audit table:
  `python3 findminsdk.py <path_to_project> -m <minSdk>`
- Generate transitive dependency resolutionStrategy block:
  `python3 findminsdk.py <path_to_project> -m <minSdk> --constraints`
- Audit single dependency:
  `python3 findminsdk.py <group:artifact> -m <minSdk>`

## Agent Workflow for Modular Projects

1. Run `python3 findminsdk.py <project_path> -m <minSdk> --json`.
2. Inspect the JSON array. Each target includes `file_path`, `line_number`, and `version_ref`.
3. Edit the identified files:
   - For `libs.versions.toml`: update the alias key in `[versions]`.
     Add `#noinspection NewerVersionAvailable, GradleDependency` ONLY if `requires_noinspection` is true (the library cannot upgrade due to minSdk).
     NEVER add `#noinspection` to JARs or libraries where `requires_noinspection` is false.
   - For module `build.gradle(.kts)` or convention plugins: update inline versions.
     Add `//noinspection NewerVersionAvailable, GradleDependency` ONLY if `requires_noinspection` is true.
   - For transitive conflicts: add the `resolutionStrategy` block to the root build file.

## Definition of "Locked" and Comments

- A dependency is "locked" ONLY when newer versions exist but cannot be upgraded because minSdk restricts it (directly or via a shared version.ref/family like io.grpc).
- Independent JAR dependencies are never locked.
- Sibling JARs sharing a family or version.ref with a locked AAR inherit the ceiling and receive `requires_noinspection: true`.
- Always trust `requires_noinspection`: add suppression ONLY when `requires_noinspection` is true.
- When `status` is `UPGRADE_AVAILABLE` and `requires_noinspection` is false, upgrade cleanly without comments.
