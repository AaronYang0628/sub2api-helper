"""Release-version consistency, package-resource loading and precise metadata updates."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import version


PROJECT = Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "helper").mkdir()
        (self.root / "ui").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "helper/__init__.py").write_text((PROJECT / "helper/__init__.py").read_text())
        (self.root / "helper/VERSION").write_text("1.2.3\n")
        (self.root / "compose.yaml").write_text("services:\n  helper:\n    image: sub2api-helper:1.2.3 # release image\n  other:\n    image: python:3.12\n")
        self.package = {"name": "helper-ui", "version": "1.2.3", "private": True, "devDependencies": {"tailwindcss": "3.4.17"}}
        self.lock = {"name": "helper-ui", "version": "1.2.3", "lockfileVersion": 3, "packages": {
            "": {"name": "helper-ui", "version": "1.2.3", "devDependencies": {"tailwindcss": "3.4.17"}},
            "node_modules/tailwindcss": {"version": "3.4.17", "integrity": "sha512-preserved", "resolved": "https://registry.example/tailwindcss.tgz"},
            "node_modules/dependency": {"version": "1.2.3", "integrity": "sha512-do-not-update"},
        }}
        (self.root / "ui/package.json").write_text(json.dumps(self.package, indent=2) + "\n")
        (self.root / "ui/package-lock.json").write_text(json.dumps(self.lock, indent=2) + "\n")
        (self.root / "README.md").write_text(
            "# Helper\n\nUpstream reviewed version: 0.2.22\n\n"
            "## Docker 部署\n\n```sh\ndocker build -t sub2api-helper:1.2.3 .\n"
            "docker run sub2api-helper:1.2.3\n```\n\n"
            "## Compatibility history\n\nPrevious image: sub2api-helper:1.2.3\n\n"
            "## 本地开发与验证\n\n```sh\npython tests/container_smoke.py sub2api-helper:1.2.3\n```\n\n"
            "## Changelog\n\nHistorical command: docker run sub2api-helper:1.2.3\n"
        )
        (self.root / "docs/upstream-baseline.json").write_text('{"reviewed_version":"0.2.22","reviewed_commit":"keep-this-evidence"}\n')
        self.paths = [self.root / name for name in ("helper/VERSION", "helper/__init__.py", "compose.yaml", "ui/package.json", "ui/package-lock.json", "README.md", "docs/upstream-baseline.json")]

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {path.relative_to(self.root): path.read_bytes() for path in self.paths}

    def test_consistent_current_metadata_passes_and_runtime_reads_canonical_resource(self):
        self.assertEqual(version.read_version(self.root), "1.2.3")
        self.assertEqual(version.check_versions(self.root), [])
        self.assertEqual(version._runtime_version(PROJECT), version.read_version(PROJECT))

    def test_setting_version_updates_only_owned_metadata_and_live_readme_examples(self):
        before = self.snapshot()
        version.set_version("0.2.22", self.root)
        self.assertEqual(version.check_versions(self.root), [])
        self.assertEqual((self.root / "helper/VERSION").read_text(), "0.2.22\n")
        compose = (self.root / "compose.yaml").read_text()
        self.assertIn("image: sub2api-helper:0.2.22 # release image", compose)
        self.assertIn("image: python:3.12", compose)
        package = json.loads((self.root / "ui/package.json").read_text())
        lock = json.loads((self.root / "ui/package-lock.json").read_text())
        expected_package = deepcopy(self.package)
        expected_package["version"] = "0.2.22"
        expected_lock = deepcopy(self.lock)
        expected_lock["version"] = expected_lock["packages"][""]["version"] = "0.2.22"
        self.assertEqual(package, expected_package)
        self.assertEqual(lock, expected_lock)
        readme = (self.root / "README.md").read_text()
        self.assertIn("docker build -t sub2api-helper:0.2.22 .", readme)
        self.assertIn("python tests/container_smoke.py sub2api-helper:0.2.22", readme)
        self.assertIn("Previous image: sub2api-helper:1.2.3", readme)
        self.assertIn("Historical command: docker run sub2api-helper:1.2.3", readme)
        self.assertEqual((self.root / "docs/upstream-baseline.json").read_bytes(), before[Path("docs/upstream-baseline.json")])
        self.assertEqual((self.root / "helper/__init__.py").read_bytes(), before[Path("helper/__init__.py")])

    def test_set_repairs_drifted_package_and_image_versions_without_dependency_updates(self):
        package = deepcopy(self.package)
        package["version"] = "0.1.0"
        lock = deepcopy(self.lock)
        lock["version"] = lock["packages"][""]["version"] = "0.1.0"
        (self.root / "ui/package.json").write_text(json.dumps(package))
        (self.root / "ui/package-lock.json").write_text(json.dumps(lock))
        (self.root / "helper/VERSION").write_text("0.2.22\n")
        self.assertTrue(version.check_versions(self.root))
        version.set_version("0.2.22", self.root)
        self.assertEqual(version.check_versions(self.root), [])
        after = json.loads((self.root / "ui/package-lock.json").read_text())
        self.assertEqual(after["packages"]["node_modules/dependency"], self.lock["packages"]["node_modules/dependency"])

    def test_invalid_release_versions_never_partially_modify_files(self):
        before = self.snapshot()
        for invalid in (None, 123, "", "v1.2.3", "01.2.3", "1.02.3", "1.2.03", "1.2", "1.2.3.4", "1.2.3-rc1", "1.2.3+build", " 1.2.3", "1.2.3\n", "1.2.3; echo surprise"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    version.set_version(invalid, self.root)
                self.assertEqual(self.snapshot(), before)

    def test_malformed_release_inputs_are_rejected_before_any_file_is_written(self):
        (self.root / "ui/package-lock.json").write_text('{"version":"1.2.3","packages":{"missing-root":{}}}')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            version.set_version("2.0.0", self.root)
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_compose_image_prevents_partial_update(self):
        path = self.root / "compose.yaml"
        path.write_text(path.read_text() + "  duplicate:\n    image: sub2api-helper:1.2.3\n")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            version.set_version("2.0.0", self.root)
        self.assertEqual(self.snapshot(), before)

    def test_setting_the_same_version_is_repeatable(self):
        version.set_version("2.0.0", self.root)
        before = self.snapshot()
        version.set_version("2.0.0", self.root)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(version.check_versions(self.root), [])

    def test_check_detects_each_derived_version_drifting_from_canonical(self):
        modifications = {
            "compose.yaml": lambda value: value.replace("sub2api-helper:1.2.3", "sub2api-helper:9.9.9"),
            "ui/package.json": lambda value: value.replace('"version": "1.2.3"', '"version": "9.9.9"'),
            "ui/package-lock.json": lambda value: value.replace('"version": "1.2.3"', '"version": "9.9.9"', 1),
            "README.md": lambda value: value.replace("docker run sub2api-helper:1.2.3", "docker run sub2api-helper:9.9.9", 1),
            "helper/__init__.py": lambda value: '__version__ = "9.9.9"\n',
        }
        for relative, modify in modifications.items():
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_text()
                path.write_text(modify(original))
                errors = version.check_versions(self.root)
                self.assertTrue(errors)
                self.assertTrue(any(("helper runtime" if relative.endswith("__init__.py") else relative) in error for error in errors), errors)
                path.write_text(original)
        self.assertEqual(version.check_versions(self.root), [])

    def test_check_detects_package_lock_root_version_even_when_top_level_matches(self):
        lock = deepcopy(self.lock)
        lock["packages"][""]["version"] = "9.9.9"
        (self.root / "ui/package-lock.json").write_text(json.dumps(lock))
        self.assertTrue(any("ui/package-lock.json" in error for error in version.check_versions(self.root)))

    def test_historical_versions_do_not_trigger_current_consistency_failure(self):
        path = self.root / "README.md"
        path.write_text(path.read_text() + "\nOld unrelated version: sub2api-helper:9.9.9\n")
        self.assertEqual(version.check_versions(self.root), [])

    def test_release_tag_must_exactly_match_canonical_version(self):
        for tag in ("1.2.3", "v1.2.3"):
            version.check_tag(tag, "1.2.3")
        for tag in ("v1.2.4", "1.2.4", "V1.2.3", "v01.2.3", "v1.2.3-rc1", "v1.2.3;echo surprise"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                version.check_tag(tag, "1.2.3")

    def test_cli_rejects_misleading_tag_before_setting_a_new_version(self):
        before = self.snapshot()
        with patch.object(version, "ROOT", self.root), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = version.main(["--set", "2.0.0", "--tag", "v1.2.3"])
        self.assertEqual(code, 2)
        self.assertEqual(self.snapshot(), before)

    def test_cli_check_outputs_only_canonical_version_on_success(self):
        output = io.StringIO()
        with patch.object(version, "ROOT", self.root), redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = version.main(["--check", "--tag", "v1.2.3"])
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "1.2.3\n")

    def test_package_resource_reads_from_an_archive_without_a_checkout_version_file(self):
        archive = self.root / "helper.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(self.root / "helper/__init__.py", "helper/__init__.py")
            output.writestr("helper/VERSION", "3.4.5\n")
        result = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0,sys.argv[1]); from helper import __version__; print(__version__)", str(archive)],
                                cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3.4.5")

    def test_corrupt_or_prerelease_canonical_version_prevents_runtime_import(self):
        for invalid in ("01.2.3\n", "1.2.3-rc1\n", "1.2.3\nextra\n"):
            with self.subTest(invalid=invalid):
                (self.root / "helper/VERSION").write_text(invalid)
                errors = version.check_versions(self.root)
                self.assertTrue(errors)
                self.assertTrue(errors[0].startswith("helper/VERSION:"))
                result = subprocess.run([sys.executable, "-c", "import helper"], cwd=self.root, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("MAJOR.MINOR.PATCH", result.stderr)


if __name__ == "__main__":
    unittest.main()
