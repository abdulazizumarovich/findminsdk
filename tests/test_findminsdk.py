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
    ConstraintGenerator,
    DependencyTarget,
    DiskCache,
    MavenClient,
    MinSdkAnalyzer,
    ProjectScanner,
    is_prerelease,
    parse_semver,
    reconcile_families,
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


class TestProjectScannerAndConstraints(unittest.TestCase):
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

    def test_scan_toml_metadata(self):
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
        self.assertEqual(dep_map["androidx.core:core"].file_path, toml_file)

        self.assertIn("androidx.activity:activity", dep_map)
        self.assertEqual(dep_map["androidx.activity:activity"].current_version, "1.13.0")
        self.assertIsNone(dep_map["androidx.activity:activity"].version_ref)

    def test_scan_multimodule_gradle(self):
        # Submodule 1: :app
        app_file = self.root / "app" / "build.gradle.kts"
        app_file.parent.mkdir(parents=True)
        app_file.write_text("""dependencies {
    implementation("androidx.core:core:1.19.0")
}
""")
        # Submodule 2: :feature:login
        feature_file = self.root / "feature" / "login" / "build.gradle.kts"
        feature_file.parent.mkdir(parents=True)
        feature_file.write_text("""dependencies {
    implementation("androidx.appcompat:appcompat:1.8.0")
}
""")
        scanner = ProjectScanner(self.root)
        deps = scanner.scan_dependencies()
        self.assertEqual(len(deps), 2)
        dep_names = {f"{d.group}:{d.artifact}" for d in deps}
        self.assertIn("androidx.core:core", dep_names)
        self.assertIn("androidx.appcompat:appcompat", dep_names)

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
        groovy, kotlin = ConstraintGenerator.generate_resolution_strategy([res], 21)
        self.assertIn("resolutionStrategy.force(", groovy)
        self.assertIn("'androidx.core:core:1.17.0'", groovy)
        self.assertIn("resolutionStrategy {", kotlin)
        self.assertIn("force(", kotlin)
        self.assertIn('"androidx.core:core:1.17.0"', kotlin)


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


class TestJarHandling(unittest.TestCase):
    def test_jar_upgrade_available(self):
        target = DependencyTarget(group="com.google.code.gson", artifact="gson", current_version="2.9.1")
        client = MavenClient()
        analyzer = MinSdkAnalyzer(client=client)
        res = analyzer.analyze(target, 21)
        self.assertTrue(res.is_jar_only)
        self.assertFalse(res.is_locked)
        self.assertEqual(res.status, "UPGRADE_AVAILABLE")
        self.assertEqual(res.max_compatible_version, res.latest_overall_version)
        self.assertIn("safe to upgrade", res.message)

    def test_jar_already_latest(self):
        target = DependencyTarget(group="com.google.code.gson", artifact="gson", current_version="2.14.0")
        client = MavenClient()
        analyzer = MinSdkAnalyzer(client=client)
        res = analyzer.analyze(target, 21)
        self.assertTrue(res.is_jar_only)
        self.assertFalse(res.is_locked)
        self.assertEqual(res.status, "OK")

    def test_aar_locked_requires_noinspection(self):
        target = DependencyTarget(group="androidx.core", artifact="core", current_version="1.17.0")
        client = MavenClient()
        analyzer = MinSdkAnalyzer(client=client)
        res = analyzer.analyze(target, 21)
        self.assertFalse(res.is_jar_only)
        self.assertTrue(res.is_locked)
        self.assertEqual(res.status, "LOCKED")


class TestFamilyReconciliation(unittest.TestCase):
    def test_reconcile_by_family_prefix(self):
        t1 = DependencyTarget(group="io.grpc", artifact="grpc-android", current_version="1.37.0")
        r1 = AnalysisResult(
            target=t1,
            target_min_sdk=21,
            current_version="1.37.0",
            current_min_sdk=16,
            latest_overall_version="1.84.0",
            latest_overall_min_sdk=24,
            max_compatible_version="1.75.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            is_locked=True,
            status="NEEDS_PIN",
        )
        t2 = DependencyTarget(group="io.grpc", artifact="grpc-okhttp", current_version="1.37.0")
        r2 = AnalysisResult(
            target=t2,
            target_min_sdk=21,
            current_version="1.37.0",
            current_min_sdk=None,
            latest_overall_version="1.84.0",
            latest_overall_min_sdk=None,
            max_compatible_version="1.84.0",
            max_compatible_min_sdk=None,
            is_jar_only=True,
            is_locked=False,
            status="UPGRADE_AVAILABLE",
        )
        reconciled = reconcile_families([r1, r2], 21)
        r2_rec = next(r for r in reconciled if r.target.artifact == "grpc-okhttp")
        self.assertEqual(r2_rec.max_compatible_version, "1.75.0")
        self.assertTrue(r2_rec.is_locked)
        self.assertEqual(r2_rec.status, "NEEDS_PIN")

    def test_reconcile_by_shared_version_ref(self):
        p = Path("/mock/libs.versions.toml")
        t1 = DependencyTarget(group="com.example", artifact="lib-aar", current_version="1.0.0", version_ref="sharedLib", file_path=p)
        r1 = AnalysisResult(
            target=t1,
            target_min_sdk=21,
            current_version="1.0.0",
            current_min_sdk=21,
            latest_overall_version="2.0.0",
            latest_overall_min_sdk=23,
            max_compatible_version="1.5.0",
            max_compatible_min_sdk=21,
            is_jar_only=False,
            is_locked=True,
            status="NEEDS_PIN",
        )
        t2 = DependencyTarget(group="com.example", artifact="lib-jar", current_version="1.0.0", version_ref="sharedLib", file_path=p)
        r2 = AnalysisResult(
            target=t2,
            target_min_sdk=21,
            current_version="1.0.0",
            current_min_sdk=None,
            latest_overall_version="2.0.0",
            latest_overall_min_sdk=None,
            max_compatible_version="2.0.0",
            max_compatible_min_sdk=None,
            is_jar_only=True,
            is_locked=False,
            status="UPGRADE_AVAILABLE",
        )
        reconciled = reconcile_families([r1, r2], 21)
        r2_rec = next(r for r in reconciled if r.target.artifact == "lib-jar")
        self.assertEqual(r2_rec.max_compatible_version, "1.5.0")
        self.assertTrue(r2_rec.is_locked)
        self.assertEqual(r2_rec.status, "NEEDS_PIN")


if __name__ == "__main__":
    unittest.main()
