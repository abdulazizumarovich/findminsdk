# findminsdk

> Fast, cross-platform Android dependency analyzer for `minSdk` compatibility.

Google recently raised `minSdkVersion` to 23 across AndroidX and Google Play services. If you develop for devices locked to **API 21** (like Android POS terminals, embedded smart kiosks, or legacy enterprise hardware), upgrading libraries triggers Gradle manifest merger errors:

```text
Manifest merger failed : uses-sdk:minSdkVersion 21 cannot be smaller than
version 23 declared in library [androidx.core:core:1.18.0]
```

`findminsdk` inspects Maven repositories directly, extracts the true `minSdkVersion` from AAR manifests via HTTP Range requests without downloading full archives, and finds the exact highest compatible version for your target `minSdk`.

In complex multi-module projects, file editing is delegated to AI coding assistants (Claude, Codex, Cursor, etc.) using structured `--json` output, avoiding brittle script modifications.

---

## Features

- **Zero dependencies**: Pure Python standard library (cross-platform: Linux, macOS, Windows).
- **Ultra-fast HTTP Range inspection**: Reads AAR zip manifests in single partial requests (milliseconds per library).
- **Persistent local cache**: Subsequent scans run in sub-second time.
- **Project auto-detection**: Detects `minSdk` and dependencies from `libs.versions.toml`, `build.gradle.kts`, and `build.gradle`.
- **Modular project friendly**: Emits file paths, line numbers, and version catalog references in JSON for AI assistants.
- **Transitive resolution strategy (`--constraints`)**: Generates Gradle `resolutionStrategy` blocks for Groovy and Kotlin DSL to prevent transitive dependency pollution.
- **AI Agent Skill included**: Built-in skill definition for coding agents and pair assistants.

---

## Quick Start

### 1. Direct Dependency Lookup

```bash
python3 findminsdk.py androidx.core:core -m 21
```

Output:
```text
Analysis for target minSdk: 21
+--------------------+---------+--------+--------+--------+-----------------+-----------+
| Dependency         | Current | CurSdk | Latest | LatSdk | Max<=21         | Status    |
+--------------------+---------+--------+--------+--------+-----------------+-----------+
| androidx.core:core | -       | -      | 1.19.0 | 23     | 1.17.0 (api 21) | NEEDS_PIN |
+--------------------+---------+--------+--------+--------+-----------------+-----------+
```

### 2. Audit an Entire Android Project

Run inside any Android project directory:

```bash
python3 /path/to/findminsdk.py
```

`findminsdk` will:
1. Auto-detect `minSdk` (e.g., `minSdk = 21` in your Gradle files).
2. Scan direct dependencies from `gradle/libs.versions.toml` and module `build.gradle(.kts)`.
3. Check all libraries against Google Maven and Maven Central concurrently.
4. Output a summary table with status for each dependency.

### 3. Structured JSON for AI Agents

```bash
python3 /path/to/findminsdk.py --json
```

Output provides file locations and version references across modular subprojects:

```json
{
  "target_min_sdk": 21,
  "dependencies": [
    {
      "group": "androidx.core",
      "artifact": "core-ktx",
      "current_version": "1.19.0",
      "latest_overall_version": "1.19.0",
      "max_compatible_version": "1.17.0",
      "version_ref": "coreKtx",
      "file_path": "/path/to/project/gradle/libs.versions.toml",
      "line_number": 9,
      "status": "NEEDS_PIN"
    }
  ]
}
```

### 4. Transitive Dependency Constraints

Transitive dependencies can still pull newer libraries that require API 23. Run with `--constraints` to generate a resolution block:

```bash
python3 /path/to/findminsdk.py -m 21 --constraints
```

#### Paste into root `build.gradle.kts` (Kotlin DSL):
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

#### Paste into root `build.gradle` (Groovy DSL):
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

---

## Command-Line Options

```text
usage: findminsdk.py [-h] [-m MIN_SDK] [--constraints]
                     [--pre-release] [--repo CUSTOM_REPOS]
                     [--no-cache] [--clear-cache] [--json]
                     [targets ...]

positional arguments:
  targets               Dependencies (e.g. androidx.core:core) or directory.

options:
  -m, --min-sdk MIN_SDK Target minSdk (e.g. 21). Auto-detected if omitted.
  --constraints         Generate Gradle resolutionStrategy constraint code.
  --pre-release         Include alpha/beta/rc releases in search.
  --repo CUSTOM_REPOS   Add custom Maven repository URL.
  --no-cache            Bypass cache.
  --clear-cache         Wipe cache file.
  --json                Output machine-readable JSON.
```

---

## How It Works

```text
+-----------------------+     1. Range bytes=0-65535      +----------------------+
|                       | ------------------------------> |                      |
|      findminsdk       |                                 | Google Maven /       |
|                       | <------------------------------ | Maven Central        |
+-----------------------+     2. Extract Manifest XML     +----------------------+
           |
           v
+-----------------------+
| Parse minSdkVersion   |
| (e.g. minSdk = 23)    |
+-----------------------+
           |
           v
+-----------------------+     Scan backwards across       +----------------------+
| Is minSdk <= Target?  | --- semver releases ----------> | Find latest version  |
+-----------------------+                                 | supporting target    |
           |                                              | (e.g. 1.17.0 = 21)   |
           v                                              +----------------------+
+-----------------------+                                            |
| Emit structured JSON  | <------------------------------------------+
| or table for AI agent |
+-----------------------+
```

---

## AI Agent Skill

This repository includes a skill definition in `.agents/skills/findminsdk/SKILL.md` (and `skills/findminsdk/SKILL.md`).

Compatible with Claude, Codex, Cursor, and other agent frameworks. Coding assistants can invoke `findminsdk` whenever `minSdk` or manifest merger issues occur.
