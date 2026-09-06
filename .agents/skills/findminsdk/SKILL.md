---
name: findminsdk
description: >-
  Analyze Android dependencies for minSdk compatibility, find the latest library
  versions supporting a target minSdk (such as API 21 for terminals and legacy devices),
  and guide the AI agent to update modular Gradle projects.
---

# findminsdk

Analyze Android dependencies to find the highest version compatible with a target `minSdk` and guide project file updates.

## Common Problem

Google recently raised `minSdk` to 23 across AndroidX and Google libraries. Projects requiring older baselines (such as API 21 for POS terminals or embedded Android devices) fail to build:

```text
Manifest merger failed : uses-sdk:minSdkVersion 21 cannot be smaller than
version 23 declared in library [androidx.core:core:1.18.0]
```

## Agent Procedure

### 1. Run Analysis

Run `findminsdk.py` with `--json` on the project root:

```bash
python3 findminsdk.py /path/to/android/project -m 21 --json
```

If `-m` is omitted, the tool auto-detects `minSdk` from project Gradle or catalog files.

### 2. Inspect Structured Output

The JSON payload provides exact file locations, line numbers, and version references for multi-module architectures:

```json
{
  "group": "androidx.core",
  "artifact": "core-ktx",
  "current_version": "1.19.0",
  "max_compatible_version": "1.17.0",
  "version_ref": "coreKtx",
  "file_path": ".../gradle/libs.versions.toml",
  "line_number": 9,
  "status": "NEEDS_PIN"
}
```

### 3. Apply Updates to Project Files

As an AI coding assistant, apply the edits contextually across the modular project:

1. **Version Catalogs (`gradle/libs.versions.toml`)**:
   Update the key referenced by `version_ref` under `[versions]` to `max_compatible_version`.
2. **Module Build Scripts (`**/build.gradle(.kts)`)**:
   For direct dependencies in submodules or convention plugins, update the version string in-place.
3. **Transitive Dependencies**:
   If a transitive library triggers manifest merger errors, add a `resolutionStrategy` to the root `build.gradle(.kts)`.

### 4. Transitive Constraint Snippets

Run with `--constraints` to get ready-to-paste snippets:

```bash
python3 findminsdk.py /path/to/android/project -m 21 --constraints
```

#### Kotlin DSL (`build.gradle.kts`)
```kotlin
allprojects {
    configurations.all {
        resolutionStrategy.eachDependency {
            if (requested.group == "androidx.core" && requested.name == "core") {
                useVersion("1.17.0")
                because("minSdk 21 compatibility")
            }
        }
    }
}
```

#### Groovy DSL (`build.gradle`)
```groovy
allprojects {
    configurations.all {
        resolutionStrategy.eachDependency { DependencyResolveDetails details ->
            if (details.requested.group == 'androidx.core' && details.requested.name == 'core') {
                details.useVersion '1.17.0'
                details.because 'minSdk 21 compatibility'
            }
        }
    }
}
```

## Options Reference

| Flag | Purpose |
| :--- | :--- |
| `-m`, `--min-sdk` | Target minSdk integer (e.g., `21`) |
| `--json` | Return structured JSON with file paths and version refs |
| `--constraints` | Print Gradle resolutionStrategy block |
| `--pre-release` | Include alpha/beta/rc releases |
| `--no-cache` | Ignore local cache |
| `--clear-cache` | Wipe local cache |
