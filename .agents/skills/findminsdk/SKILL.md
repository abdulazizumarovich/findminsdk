---
name: findminsdk
description: >-
  Analyze Android dependencies for minSdk compatibility, find the latest library
  versions supporting a target minSdk (such as API 21 for terminals and legacy devices),
  and automatically lock Gradle scripts to resolve Manifest merger warnings and errors.
---

# findminsdk

Analyze Android dependencies to find the highest version compatible with a target `minSdk` and pin dependencies to eliminate Gradle manifest merger collisions.

## Common Problem

Google recently raised `minSdk` to 23 across AndroidX and Google libraries. Projects requiring older baselines (such as API 21 for POS terminals or embedded Android devices) fail to build:

```text
Manifest merger failed : uses-sdk:minSdkVersion 21 cannot be smaller than
version 23 declared in library [androidx.core:core:1.18.0]
```

## Workflows

### 1. Direct Dependency Lookup

Check the maximum compatible version for any dependency:

```bash
python3 findminsdk.py androidx.core:core -m 21
python3 findminsdk.py androidx.appcompat:appcompat androidx.activity:activity -m 21
```

### 2. Audit Project Dependencies

Scan an Android project directory to identify incompatible libraries:

```bash
python3 findminsdk.py /path/to/android/project -m 21
```

If `-m` is omitted, the tool auto-detects `minSdk` from `build.gradle`, `build.gradle.kts`, or `libs.versions.toml`.

### 3. Automatically Lock Incompatible Dependencies

Pin the max compatible version in `libs.versions.toml` or `build.gradle(.kts)`:

```bash
python3 findminsdk.py /path/to/android/project -m 21 --lock
```

### 4. Lock Transitive Dependencies

When transitive dependencies bring higher `minSdk` libraries into the build, enforce constraints in the root Gradle script using `--constraints`:

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
| `--lock`, `--fix` | Rewrite project files to max compatible versions |
| `--constraints` | Print Gradle resolutionStrategy block |
| `--json` | Return structured JSON output |
| `--pre-release` | Include alpha/beta/rc releases |
| `--no-cache` | Ignore local cache |
| `--clear-cache` | Wipe local cache |
