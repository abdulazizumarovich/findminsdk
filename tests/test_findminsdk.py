#!/usr/bin/env python3
import io
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from findminsdk import (
    AnalysisResult,
    DependencyTarget,
    DiskCache,
    MavenClient,
    MinSdkAnalyzer,
    ProjectLocker,
    ProjectScanner,
    is_prerelease,
    parse_semver,
)


class TestSemver(unittest.TestCase):
    def test_parse_semver(self):
        v1 = parse_semver("1.17.0")
        v2 = parse_semver("1.18.0")
        v3 = parse_semver("1.18.0-alpha01")
        self.assertTrue(v1 < v2)
        self.assertTrue(v3 < v2)
        self.assertTrue(parse_semver("1.2.3") == (1, 2, 3, 1, ""))

    def test_is_prerelease(self):
        self.assertTrue(is_prerelease("1.0.0-alpha01"))
        self.assertTrue(is_prerelease("2.1.0-rc01"))
        self.assertTrue(is_prerelease("1.5.0-beta02"))
        self.assertFalse(is_prerelease("1.17.0"))
        self.assertFalse(is_prerelease("2.0.1"))


class TestManifestExtraction(unittest.TestCase):
    def test_scan_chunk_for_manifest(self):
        # Create a mock AAR with AndroidManifest.xml
        manifest_xml = b'<?xml version="1.0"?><manifest xmlns:android="http://schemas.android.com/apk/res/android"><uses-sdk android:minSdkVersion="21"/></manifest>'
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("R.txt", b"int foo 0x0")
            z.writestr("AndroidManifest.xml", manifest_xml)
        bio.seek(0)
        chunk = bio.read()

        client = MavenClient()
        sdk = client._scan_chunk_for_manifest(chunk)
        self.assertEqual(sdk, 21)


class TestProjectScannerAndLocker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_project_min_sdk_kts(self):
        build_kts = self.root / "app" / "build.gradle.kts"
        build_kts.parent.mkdir(parents=True)
        build_kts.write_text("""
android {
    defaultConfig {
        minSdk = 21
        targetSdk = 34
    }
}
""")
        scanner = ProjectScanner(self.root)
        self.assertEqual(scanner.detect_project_min_sdk(), 21)

    def test_detect_project_min_sdk_groovy(self):
        build_groovy = self.root / "app" / "build.gradle"
        build_groovy.parent.mkdir(parents=True)
        build_groovy.write_text("""
android {
    defaultConfig {
        minSdkVersion 21
    }
}
""")
        scanner = ProjectScanner(self.root)
        self.assertEqual(scanner.detect_project_min_sdk(), 21)

    def test_scan_and_lock_toml(self):
        gradle_dir = self.root / "gradle"
        gradle_dir.mkdir(parents=True)
        toml_file = gradle_dir / "libs.versions.toml"
        toml_file.write_text("""[versions]
core = "1.19.0"
appcompat = "1.8.0"

[libraries]
androidx-core = { group = "androidx.core", name = "core", version.ref = "core" }
androidx-appcompat = { group = "androidx.appcompat", name = "appcompat", version.ref = "appcompat" }
inline-dep = { group = "androidx.activity", name = "activity", version = "1.13.0" }
""")

        scanner = ProjectScanner(self.root)
        deps = scanner.scan_dependencies()
        dep_map = {f"{d.group}:{d.artifact}": d for d in deps}

        self.assertIn("androidx.core:core", dep_map)
        self.assertEqual(dep_map["androidx.core:core"].current_version, "1.19.0")
        self.assertEqual(dep_map["androidx.core:core"].version_ref, "core")

        # Test Locking with version.ref
        res = AnalysisResult(
            target=dep_map["androidx.core:core"],
            target_min_sdk=21,
            current_version="1.19.0",
            current_min_sdk=23,
            latest_overall_version="1.19.0",
            latest_overall_min_sdk=23,
            max_compatible_version="1.17.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            status="NEEDS_PIN",
            message="Pin to 1.17.0",
        )
        ok = ProjectLocker.lock_dependency(res)
        self.assertTrue(ok)

        # Test Locking inline version
        res_inline = AnalysisResult(
            target=dep_map["androidx.activity:activity"],
            target_min_sdk=21,
            current_version="1.13.0",
            current_min_sdk=23,
            latest_overall_version="1.13.0",
            latest_overall_min_sdk=23,
            max_compatible_version="1.11.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            status="NEEDS_PIN",
            message="Pin to 1.11.0",
        )
        ok2 = ProjectLocker.lock_dependency(res_inline)
        self.assertTrue(ok2)

        content = toml_file.read_text()
        self.assertIn('core = "1.17.0"', content)
        self.assertIn('version = "1.11.0"', content)

    def test_scan_and_lock_gradle(self):
        build_file = self.root / "app" / "build.gradle.kts"
        build_file.parent.mkdir(parents=True)
        build_file.write_text("""dependencies {
    implementation("androidx.core:core:1.19.0")
}
""")
        scanner = ProjectScanner(self.root)
        deps = scanner.scan_dependencies()
        self.assertEqual(len(deps), 1)

        res = AnalysisResult(
            target=deps[0],
            target_min_sdk=21,
            current_version="1.19.0",
            current_min_sdk=23,
            latest_overall_version="1.19.0",
            latest_overall_min_sdk=23,
            max_compatible_version="1.17.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            status="NEEDS_PIN",
            message="Pin to 1.17.0",
        )
        ok = ProjectLocker.lock_dependency(res)
        self.assertTrue(ok)

        content = build_file.read_text()
        self.assertIn('implementation("androidx.core:core:1.17.0")', content)

    def test_resolution_strategy_generation(self):
        target = DependencyTarget(group="androidx.core", artifact="core")
        res = AnalysisResult(
            target=target,
            target_min_sdk=21,
            current_version="1.19.0",
            current_min_sdk=23,
            latest_overall_version="1.19.0",
            latest_overall_min_sdk=23,
            max_compatible_version="1.17.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            status="NEEDS_PIN",
            message="Pin to 1.17.0",
        )
        groovy, kotlin = ProjectLocker.generate_resolution_strategy([res], 21)
        self.assertIn("details.requested.group == 'androidx.core'", groovy)
        self.assertIn("details.useVersion '1.17.0'", groovy)
        self.assertIn('requested.group == "androidx.core"', kotlin)
        self.assertIn('useVersion("1.17.0")', kotlin)


class TestDiskCache(unittest.TestCase):
    def test_cache_operations(self):
        with tempfile.NamedTemporaryFile() as tmp:
            c = DiskCache(cache_file=Path(tmp.name))
            c.set("androidx.core", "core", "1.17.0", 21, True)
            c.save()

            c2 = DiskCache(cache_file=Path(tmp.name))
            val = c2.get("androidx.core", "core", "1.17.0")
            self.assertIsNotNone(val)
            self.assertEqual(val["min_sdk"], 21)
            self.assertTrue(val["is_aar"])

            c2.clear()
            self.assertIsNone(c2.get("androidx.core", "core", "1.17.0"))


if __name__ == "__main__":
    unittest.main()
