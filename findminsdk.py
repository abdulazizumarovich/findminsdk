#!/usr/bin/env python3
"""
findminsdk - Android Dependency minSdk Analyzer and Version Locker.

Finds the latest dependency versions compatible with a target minSdk
(e.g., minSdk 21 for older devices and payment terminals) and locks them
in Gradle build files or Version Catalogs.
"""

import argparse
import concurrent.futures
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

DEFAULT_REPOSITORIES = [
    "https://dl.google.com/dl/android/maven2",
    "https://repo1.maven.org/maven2",
]

CACHE_FILE = Path.home() / ".cache" / "findminsdk" / "cache.json"


@dataclass
class VersionInfo:
    version: str
    min_sdk: Optional[int]
    is_aar: bool


@dataclass
class DependencyTarget:
    group: str
    artifact: str
    current_version: Optional[str] = None
    version_ref: Optional[str] = None
    file_path: Optional[Path] = None
    line_number: Optional[int] = None


@dataclass
class AnalysisResult:
    target: DependencyTarget
    target_min_sdk: int
    current_version: Optional[str]
    current_min_sdk: Optional[int]
    latest_overall_version: Optional[str]
    latest_overall_min_sdk: Optional[int]
    max_compatible_version: Optional[str]
    max_compatible_min_sdk: Optional[int]
    is_jar_only: bool
    status: str
    message: str


class DiskCache:
    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self.data: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def save(self):
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def get(self, group: str, artifact: str, version: str) -> Optional[Dict]:
        key = f"{group}:{artifact}:{version}"
        return self.data.get(key)

    def set(self, group: str, artifact: str, version: str, min_sdk: Optional[int], is_aar: bool):
        key = f"{group}:{artifact}:{version}"
        self.data[key] = {"min_sdk": min_sdk, "is_aar": is_aar}

    def clear(self):
        self.data = {}
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except Exception:
                pass


def parse_semver(v: str) -> Tuple:
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.\-]([a-zA-Z0-9.\-_]+))?$", v)
    if not m:
        return (0, 0, 0, 0, v)
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    suffix = m.group(4)
    is_stable = 1 if suffix is None else 0
    return (major, minor, patch, is_stable, suffix or "")


def is_prerelease(version: str) -> bool:
    lower = version.lower()
    return any(p in lower for p in ["alpha", "beta", "rc", "dev", "snapshot", "preview", "m1", "m2"])


class MavenClient:
    def __init__(self, repositories: Optional[List[str]] = None, cache: Optional[DiskCache] = None):
        self.repos = repositories or list(DEFAULT_REPOSITORIES)
        self.cache = cache or DiskCache()
        self.user_agent = "findminsdk/1.0"

    def fetch_versions(self, group: str, artifact: str) -> Tuple[Optional[str], List[str]]:
        group_path = group.replace(".", "/")
        for base in self.repos:
            meta_url = f"{base}/{group_path}/{artifact}/maven-metadata.xml"
            req = urllib.request.Request(meta_url, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content = resp.read().decode("utf-8", errors="ignore")
                versions = re.findall(r"<version>([^<]+)</version>", content)
                if versions:
                    return base, versions
            except Exception:
                continue
        return None, []

    def inspect_artifact_min_sdk(self, base_url: str, group: str, artifact: str, version: str) -> Tuple[Optional[int], bool]:
        cached = self.cache.get(group, artifact, version)
        if cached is not None:
            return cached.get("min_sdk"), cached.get("is_aar", True)

        group_path = group.replace(".", "/")
        aar_url = f"{base_url}/{group_path}/{artifact}/{version}/{artifact}-{version}.aar"

        min_sdk, is_aar = self._extract_min_sdk_from_aar(aar_url)
        if not is_aar:
            jar_url = f"{base_url}/{group_path}/{artifact}/{version}/{artifact}-{version}.jar"
            is_jar = self._check_file_exists(jar_url)
            if is_jar:
                self.cache.set(group, artifact, version, None, False)
                return None, False

        self.cache.set(group, artifact, version, min_sdk, is_aar)
        return min_sdk, is_aar

    def _check_file_exists(self, url: str) -> bool:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _extract_min_sdk_from_aar(self, aar_url: str) -> Tuple[Optional[int], bool]:
        req = urllib.request.Request(
            aar_url,
            headers={"Range": "bytes=0-65535", "User-Agent": self.user_agent},
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                chunk = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, False
            return None, False
        except Exception:
            return None, False

        min_sdk = self._scan_chunk_for_manifest(chunk)
        if min_sdk is not None:
            return min_sdk, True

        tail_sdk = self._read_manifest_from_tail(aar_url)
        if tail_sdk is not None:
            return tail_sdk, True

        return 1, True

    def _scan_chunk_for_manifest(self, chunk: bytes) -> Optional[int]:
        offset = 0
        while offset < len(chunk) - 30:
            idx = chunk.find(b"PK\x03\x04", offset)
            if idx == -1:
                break
            offset = idx
            comp_method = struct.unpack("<H", chunk[offset + 8 : offset + 10])[0]
            fname_len = struct.unpack("<H", chunk[offset + 26 : offset + 28])[0]
            extra_len = struct.unpack("<H", chunk[offset + 28 : offset + 30])[0]
            fname_start = offset + 30
            fname = chunk[fname_start : fname_start + fname_len].decode("utf-8", errors="ignore")
            data_start = fname_start + fname_len + extra_len
            if fname == "AndroidManifest.xml":
                data = chunk[data_start:]
                try:
                    if comp_method == 8:
                        xml = zlib.decompressobj(-15).decompress(data).decode("utf-8", errors="ignore")
                    else:
                        xml = data[:8192].decode("utf-8", errors="ignore")
                    m = re.search(r'android:minSdkVersion="(\d+)"', xml)
                    return int(m.group(1)) if m else 1
                except Exception:
                    pass
            offset = data_start + 1
        return None

    def _read_manifest_from_tail(self, url: str) -> Optional[int]:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                total_len = int(resp.headers.get("Content-Length", 0))
        except Exception:
            return None

        if total_len <= 0:
            return None

        tail_len = min(65536, total_len)
        req = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={total_len - tail_len}-{total_len - 1}",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                tail = resp.read()
        except Exception:
            return None

        eocd_idx = tail.rfind(b"PK\x05\x06")
        if eocd_idx == -1 or len(tail) < eocd_idx + 20:
            return None

        cd_size = struct.unpack("<I", tail[eocd_idx + 12 : eocd_idx + 16])[0]
        cd_offset = struct.unpack("<I", tail[eocd_idx + 16 : eocd_idx + 20])[0]

        req = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                cd_data = resp.read()
        except Exception:
            return None

        cd_pos = 0
        while cd_pos < len(cd_data) - 46:
            if cd_data[cd_pos : cd_pos + 4] != b"PK\x01\x02":
                break
            comp_method = struct.unpack("<H", cd_data[cd_pos + 10 : cd_pos + 12])[0]
            comp_size = struct.unpack("<I", cd_data[cd_pos + 20 : cd_pos + 24])[0]
            fname_len = struct.unpack("<H", cd_data[cd_pos + 28 : cd_pos + 30])[0]
            extra_len = struct.unpack("<H", cd_data[cd_pos + 30 : cd_pos + 32])[0]
            comment_len = struct.unpack("<H", cd_data[cd_pos + 32 : cd_pos + 34])[0]
            local_hdr_offset = struct.unpack("<I", cd_data[cd_pos + 42 : cd_pos + 46])[0]
            fname = cd_data[cd_pos + 46 : cd_pos + 46 + fname_len].decode("utf-8", errors="ignore")

            if fname == "AndroidManifest.xml":
                fetch_len = 30 + fname_len + 128 + comp_size
                req = urllib.request.Request(
                    url,
                    headers={
                        "Range": f"bytes={local_hdr_offset}-{local_hdr_offset + fetch_len - 1}",
                        "User-Agent": self.user_agent,
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        lchunk = resp.read()
                    l_fname_len = struct.unpack("<H", lchunk[26:28])[0]
                    l_extra_len = struct.unpack("<H", lchunk[28:30])[0]
                    data = lchunk[30 + l_fname_len + l_extra_len : 30 + l_fname_len + l_extra_len + comp_size]
                    if comp_method == 8:
                        xml = zlib.decompressobj(-15).decompress(data).decode("utf-8", errors="ignore")
                    else:
                        xml = data.decode("utf-8", errors="ignore")
                    m = re.search(r'android:minSdkVersion="(\d+)"', xml)
                    return int(m.group(1)) if m else 1
                except Exception:
                    return None

            cd_pos += 46 + fname_len + extra_len + comment_len

        return None


class MinSdkAnalyzer:
    def __init__(self, client: MavenClient, include_prerelease: bool = False):
        self.client = client
        self.include_prerelease = include_prerelease

    def analyze(self, target: DependencyTarget, target_min_sdk: int) -> AnalysisResult:
        base_url, all_versions = self.client.fetch_versions(target.group, target.artifact)
        if not base_url or not all_versions:
            return AnalysisResult(
                target=target,
                target_min_sdk=target_min_sdk,
                current_version=target.current_version,
                current_min_sdk=None,
                latest_overall_version=None,
                latest_overall_min_sdk=None,
                max_compatible_version=None,
                max_compatible_min_sdk=None,
                is_jar_only=False,
                status="NOT_FOUND",
                message=f"Artifact not found in repositories: {target.group}:{target.artifact}",
            )

        if not self.include_prerelease:
            filtered = [v for v in all_versions if not is_prerelease(v)]
            version_candidates = filtered if filtered else all_versions
        else:
            version_candidates = all_versions

        sorted_versions = sorted(version_candidates, key=parse_semver)
        latest_ver = sorted_versions[-1]

        latest_min_sdk, latest_is_aar = self.client.inspect_artifact_min_sdk(
            base_url, target.group, target.artifact, latest_ver
        )

        current_min_sdk = None
        if target.current_version:
            curr_sdk, _ = self.client.inspect_artifact_min_sdk(
                base_url, target.group, target.artifact, target.current_version
            )
            current_min_sdk = curr_sdk

        if not latest_is_aar:
            status = "UPGRADE_AVAILABLE" if target.current_version and target.current_version != latest_ver else "OK"
            msg = (
                f"Pure Java JAR: safe to upgrade to {latest_ver} (no minSdk restriction)"
                if status == "UPGRADE_AVAILABLE"
                else "Pure Java JAR at latest version (no minSdk restriction)"
            )
            return AnalysisResult(
                target=target,
                target_min_sdk=target_min_sdk,
                current_version=target.current_version,
                current_min_sdk=None,
                latest_overall_version=latest_ver,
                latest_overall_min_sdk=None,
                max_compatible_version=latest_ver,
                max_compatible_min_sdk=None,
                is_jar_only=True,
                status=status,
                message=msg,
            )

        if latest_min_sdk is not None and latest_min_sdk <= target_min_sdk:
            status = "UPGRADE_AVAILABLE" if target.current_version and target.current_version != latest_ver else "OK"
            return AnalysisResult(
                target=target,
                target_min_sdk=target_min_sdk,
                current_version=target.current_version,
                current_min_sdk=current_min_sdk,
                latest_overall_version=latest_ver,
                latest_overall_min_sdk=latest_min_sdk,
                max_compatible_version=latest_ver,
                max_compatible_min_sdk=latest_min_sdk,
                is_jar_only=False,
                status=status,
                message=f"Latest version supports minSdk {latest_min_sdk} <= {target_min_sdk}",
            )

        max_compatible_ver = None
        max_compatible_sdk = None

        for ver in reversed(sorted_versions[:-1]):
            sdk, is_aar = self.client.inspect_artifact_min_sdk(base_url, target.group, target.artifact, ver)
            if sdk is not None and sdk <= target_min_sdk:
                max_compatible_ver = ver
                max_compatible_sdk = sdk
                break

        if not max_compatible_ver:
            status = "INCOMPATIBLE"
            msg = f"No version found supporting minSdk <= {target_min_sdk}"
        elif target.current_version == max_compatible_ver:
            status = "LOCKED"
            msg = f"Already locked to max compatible version {max_compatible_ver}"
        else:
            status = "NEEDS_PIN"
            msg = f"Pin to {max_compatible_ver} to prevent minSdk {latest_min_sdk} collision"

        return AnalysisResult(
            target=target,
            target_min_sdk=target_min_sdk,
            current_version=target.current_version,
            current_min_sdk=current_min_sdk,
            latest_overall_version=latest_ver,
            latest_overall_min_sdk=latest_min_sdk,
            max_compatible_version=max_compatible_ver,
            max_compatible_min_sdk=max_compatible_sdk,
            is_jar_only=False,
            status=status,
            message=msg,
        )


class ProjectScanner:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()

    def detect_project_min_sdk(self) -> Optional[int]:
        patterns = [
            r"minSdk\s*=\s*(\d+)",
            r"minSdkVersion\s*=?\s*(\d+)",
            r"minSdk\s*\(\s*(\d+)\s*\)",
            r"minSdkVersion\s*\(\s*(\d+)\s*\)",
        ]
        gradle_files = list(self.root_dir.glob("**/build.gradle")) + list(
            self.root_dir.glob("**/build.gradle.kts")
        )
        for f in gradle_files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for pat in patterns:
                    m = re.search(pat, content)
                    if m:
                        return int(m.group(1))
            except Exception:
                continue

        toml_files = list(self.root_dir.glob("**/libs.versions.toml"))
        for t in toml_files:
            try:
                content = t.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"minSdk\s*=\s*[\"']?(\d+)[\"']?", content)
                if m:
                    return int(m.group(1))
            except Exception:
                continue

        return None

    def scan_dependencies(self) -> List[DependencyTarget]:
        deps: Dict[str, DependencyTarget] = {}

        toml_files = list(self.root_dir.glob("**/libs.versions.toml"))
        for toml_path in toml_files:
            self._scan_toml(toml_path, deps)

        gradle_files = list(self.root_dir.glob("**/build.gradle")) + list(
            self.root_dir.glob("**/build.gradle.kts")
        )
        for g_path in gradle_files:
            self._scan_gradle(g_path, deps)

        return list(deps.values())

    def _scan_toml(self, path: Path, deps: Dict[str, DependencyTarget]):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        lines = content.splitlines()
        in_versions = False
        in_libraries = False
        versions_map: Dict[str, str] = {}

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[versions]"):
                in_versions = True
                in_libraries = False
                continue
            elif stripped.startswith("["):
                in_versions = False
                in_libraries = stripped.startswith("[libraries]")
                continue

            if in_versions and "=" in stripped and not stripped.startswith("#"):
                parts = stripped.split("=", 1)
                vkey = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                versions_map[vkey] = val

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue

            g_m = re.search(r'group\s*=\s*["\']([^"\']+)["\']', stripped)
            n_m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', stripped)
            vref_m = re.search(r'version\.ref\s*=\s*["\']([^"\']+)["\']', stripped)
            vdir_m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', stripped)
            mod_m = re.search(r'module\s*=\s*["\']([^"\':]+):([^"\':]+)["\']', stripped)
            str_dep = re.search(r'=\s*["\']([^"\':]+):([^"\':]+):([^"\':]+)["\']', stripped)

            group, artifact, ver, vref = None, None, None, None

            if g_m and n_m:
                group = g_m.group(1)
                artifact = n_m.group(1)
            elif mod_m:
                group = mod_m.group(1)
                artifact = mod_m.group(2)
            elif str_dep:
                group = str_dep.group(1)
                artifact = str_dep.group(2)
                ver = str_dep.group(3)

            if vref_m:
                vref = vref_m.group(1)
                ver = versions_map.get(vref)
            elif vdir_m:
                ver = vdir_m.group(1)

            if group and artifact:
                key = f"{group}:{artifact}"
                if key not in deps:
                    deps[key] = DependencyTarget(
                        group=group,
                        artifact=artifact,
                        current_version=ver,
                        version_ref=vref,
                        file_path=path,
                        line_number=i,
                    )

    def _scan_gradle(self, path: Path, deps: Dict[str, DependencyTarget]):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        lines = content.splitlines()
        pat = re.compile(
            r'(?:implementation|api|compileOnly|runtimeOnly|coreLibraryDesugaring)\s*\(?\s*[\'"]([a-zA-Z0-9.\-_]+):([a-zA-Z0-9.\-_]+):([a-zA-Z0-9.\-_]+)[\'"]'
        )

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            m = pat.search(stripped)
            if m:
                g, a, v = m.group(1), m.group(2), m.group(3)
                key = f"{g}:{a}"
                if key not in deps:
                    deps[key] = DependencyTarget(
                        group=g,
                        artifact=a,
                        current_version=v,
                        version_ref=None,
                        file_path=path,
                        line_number=i,
                    )


class ConstraintGenerator:

    @staticmethod
    def generate_resolution_strategy(results: List[AnalysisResult], min_sdk: int) -> Tuple[str, str]:
        pins = [r for r in results if r.max_compatible_version and r.status in ("NEEDS_PIN", "LOCKED")]
        if not pins:
            return "", ""

        groovy_lines = [
            "// Paste inside root build.gradle (Groovy DSL)",
            "allprojects {",
            "    configurations.all {",
            "        resolutionStrategy.eachDependency { DependencyResolveDetails details ->",
        ]
        for p in pins:
            groovy_lines.append(
                f"            if (details.requested.group == '{p.target.group}' && details.requested.name == '{p.target.artifact}') {{"
            )
            groovy_lines.append(f"                details.useVersion '{p.max_compatible_version}'")
            groovy_lines.append(f"                details.because 'minSdk {min_sdk} compatibility'")
            groovy_lines.append("            }")
        groovy_lines.extend(["        }", "    }", "}"])

        kotlin_lines = [
            "// Paste inside root build.gradle.kts (Kotlin DSL)",
            "allprojects {",
            "    configurations.all {",
            "        resolutionStrategy.eachDependency {",
        ]
        for p in pins:
            kotlin_lines.append(
                f'            if (requested.group == "{p.target.group}" && requested.name == "{p.target.artifact}") {{'
            )
            kotlin_lines.append(f'                useVersion("{p.max_compatible_version}")')
            kotlin_lines.append(f'                because("minSdk {min_sdk} compatibility")')
            kotlin_lines.append("            }")
        kotlin_lines.extend(["        }", "    }", "}"])

        return "\n".join(groovy_lines), "\n".join(kotlin_lines)


def print_table(results: List[AnalysisResult], target_min_sdk: int):
    headers = [
        "Dependency",
        "Current",
        "CurSdk",
        "Latest",
        "LatSdk",
        f"Max<={target_min_sdk}",
        "Status",
    ]
    rows = []
    for r in results:
        dep_name = f"{r.target.group}:{r.target.artifact}"
        curr = r.current_version or "-"
        curr_sdk = str(r.current_min_sdk) if r.current_min_sdk is not None else "-"
        lat = r.latest_overall_version or "-"
        lat_sdk = str(r.latest_overall_min_sdk) if r.latest_overall_min_sdk is not None else ("JAR" if r.is_jar_only else "-")
        max_compat = r.max_compatible_version or "None"
        if r.max_compatible_min_sdk is not None:
            max_compat += f" (api {r.max_compatible_min_sdk})"
        elif r.is_jar_only:
            max_compat += " (JAR)"

        rows.append([dep_name, curr, curr_sdk, lat, lat_sdk, max_compat, r.status])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    def make_row(cells, pad=" "):
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, col_widths)) + " |"

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"

    print(f"\nAnalysis for target minSdk: {target_min_sdk}")
    print(sep)
    print(make_row(headers))
    print(sep)
    for row in rows:
        print(make_row(row))
    print(sep)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Android dependencies for minSdk compatibility and lock versions."
    )
    parser.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="Dependencies (e.g. androidx.core:core or androidx.core:core:1.19.0) or directory path. Default: current directory.",
    )
    parser.add_argument(
        "-m",
        "--min-sdk",
        type=int,
        default=None,
        help="Target minSdk (e.g. 21). Defaults to project minSdk or 21.",
    )
    parser.add_argument(
        "--constraints",
        action="store_true",
        help="Generate Gradle resolutionStrategy constraint code for build.gradle.",
    )
    parser.add_argument(
        "--pre-release",
        action="store_true",
        help="Include pre-release (alpha, beta, rc) versions in analysis.",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="custom_repos",
        help="Add custom Maven repository URL.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass cached metadata and minSdk values.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear persistent cache before running.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results.",
    )

    args = parser.parse_args()

    cache = DiskCache()
    if args.clear_cache:
        cache.clear()
        print("Cache cleared.")
        if not args.targets:
            return

    if args.no_cache:
        cache = DiskCache(cache_file=Path("/dev/null"))

    repos = list(DEFAULT_REPOSITORIES)
    if args.custom_repos:
        repos.extend(args.custom_repos)

    client = MavenClient(repositories=repos, cache=cache)
    analyzer = MinSdkAnalyzer(client=client, include_prerelease=args.pre_release)

    explicit_deps: List[DependencyTarget] = []
    scan_dir: Optional[Path] = None

    if not args.targets:
        scan_dir = Path.cwd()
    else:
        for t in args.targets:
            p = Path(t)
            if p.is_dir():
                scan_dir = p
            elif ":" in t:
                parts = t.split(":")
                g = parts[0]
                a = parts[1]
                v = parts[2] if len(parts) > 2 else None
                explicit_deps.append(DependencyTarget(group=g, artifact=a, current_version=v))
            else:
                p_file = Path(t)
                if p_file.is_file():
                    scan_dir = p_file.parent

    target_min_sdk = args.min_sdk
    targets_to_analyze: List[DependencyTarget] = list(explicit_deps)

    if scan_dir:
        scanner = ProjectScanner(scan_dir)
        detected_sdk = scanner.detect_project_min_sdk()
        if target_min_sdk is None:
            target_min_sdk = detected_sdk if detected_sdk is not None else 21
        scanned = scanner.scan_dependencies()
        targets_to_analyze.extend(scanned)

    if target_min_sdk is None:
        target_min_sdk = 21

    if not targets_to_analyze:
        print("No Android dependencies or project found to analyze.")
        sys.exit(1)

    # Deduplicate targets by group:artifact
    unique_targets: Dict[str, DependencyTarget] = {}
    for dt in targets_to_analyze:
        key = f"{dt.group}:{dt.artifact}"
        if key not in unique_targets or (dt.current_version and not unique_targets[key].current_version):
            unique_targets[key] = dt

    target_list = list(unique_targets.values())

    results: List[AnalysisResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {
            executor.submit(analyzer.analyze, t, target_min_sdk): t for t in target_list
        }
        for future in concurrent.futures.as_completed(future_map):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                t = future_map[future]
                results.append(
                    AnalysisResult(
                        target=t,
                        target_min_sdk=target_min_sdk,
                        current_version=t.current_version,
                        current_min_sdk=None,
                        latest_overall_version=None,
                        latest_overall_min_sdk=None,
                        max_compatible_version=None,
                        max_compatible_min_sdk=None,
                        is_jar_only=False,
                        status="ERROR",
                        message=str(e),
                    )
                )

    cache.save()

    results.sort(key=lambda r: f"{r.target.group}:{r.target.artifact}")

    if args.json:
        out = {
            "target_min_sdk": target_min_sdk,
            "dependencies": [
                {
                    "group": r.target.group,
                    "artifact": r.target.artifact,
                    "current_version": r.current_version,
                    "current_min_sdk": r.current_min_sdk,
                    "latest_overall_version": r.latest_overall_version,
                    "latest_overall_min_sdk": r.latest_overall_min_sdk,
                    "max_compatible_version": r.max_compatible_version,
                    "max_compatible_min_sdk": r.max_compatible_min_sdk,
                    "version_ref": r.target.version_ref,
                    "file_path": str(r.target.file_path) if r.target.file_path else None,
                    "line_number": r.target.line_number,
                    "is_jar_only": r.is_jar_only,
                    "status": r.status,
                    "message": r.message,
                }
                for r in results
            ],
        }
        print(json.dumps(out, indent=2))
        return

    print_table(results, target_min_sdk)

    if args.constraints or any(r.status == "NEEDS_PIN" for r in results):
        groovy_snip, kotlin_snip = ConstraintGenerator.generate_resolution_strategy(results, target_min_sdk)
        if groovy_snip:
            print("\n" + "=" * 60)
            print("RECOMMENDED GRADLE RESOLUTION STRATEGY (Pins transitive deps)")
            print("=" * 60)
            print("\n" + groovy_snip + "\n")
            print("-" * 60)
            print("\n" + kotlin_snip + "\n")


if __name__ == "__main__":
    main()
