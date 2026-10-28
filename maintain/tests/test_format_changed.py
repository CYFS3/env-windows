"""Integration tests using disposable repositories and the bundled Windows tools."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "tools" / "bin"
GIT = ROOT / "tools" / "git-2.41.0-32-bit" / "cmd" / "git.exe"
STYLE = ("BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\n"
         "AllowShortFunctionsOnASingleLine: None\n")
ORIGINAL = "int original(){return 0;}\n"
CHANGED = "int changed(){int value=1;return value;}\n"


class FormatChangedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="env format test ")
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(name, None)
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.env["PATH"] = str(BIN) + os.pathsep + self.env.get("PATH", "")
        self.env["PYTHONUTF8"] = "1"
        self.git("init", "-q", "--initial-branch=main", "--template=")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "Env Tests")
        self.git("config", "user.email", "env-tests@example.invalid")
        self.write(".clang-format", STYLE)
        for name in ("unstaged.c", "staged.cpp", "untouched.c", "rename.c", "deleted.c"):
            self.write(name, ORIGINAL)
        self.write("notes.txt", ORIGINAL)
        self.git("add", ".")
        self.git("commit", "-qm", "Initial test files")

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *args, check=True, input=None):
        return subprocess.run(
            [str(GIT), *args], cwd=self.repo, env=self.env,
            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
        )

    def command(self, *args, cwd=None):
        return subprocess.run(
            [self.env.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
             "/d", "/c", "format-changed", *args],
            cwd=cwd or self.repo, env=self.env, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_selection_and_index_preservation_from_subdirectory(self):
        self.write("unstaged.c", CHANGED)
        self.write("staged.cpp", CHANGED)
        self.git("add", "staged.cpp")
        index_before = self.git("show", ":staged.cpp").stdout
        self.write("staged.cpp", CHANGED + "int extra(){return 2;}\n")
        self.write("source files/驱动 & new.h", CHANGED)
        self.write("notes.txt", CHANGED)
        self.write(".gitignore", "ignored.c\n")
        self.write("ignored.c", CHANGED)
        self.git("mv", "rename.c", "renamed.c")
        self.git("rm", "-q", "deleted.c")
        untouched_before = (self.repo / "untouched.c").read_bytes()
        result = self.command(cwd=self.repo / "source files")
        self.assert_ok(result)
        self.assertIn("Selected 4 changed", result.stdout)
        for name in ("unstaged.c", "staged.cpp", "source files/驱动 & new.h", "renamed.c"):
            self.assertIn("\n{\n", (self.repo / name).read_text(encoding="utf-8"))
        self.assertEqual(self.git("show", ":staged.cpp").stdout, index_before)
        self.assertEqual((self.repo / "untouched.c").read_bytes(), untouched_before)
        for name in ("notes.txt", "ignored.c"):
            self.assertEqual((self.repo / name).read_text(encoding="utf-8"), CHANGED)
        self.assertFalse((self.repo / "deleted.c").exists())

    def test_check_does_not_write_and_passes_after_formatting(self):
        path = self.write("unstaged.c", CHANGED)
        before = path.read_bytes()
        result = self.command("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assert_ok(self.command())
        formatted = path.read_bytes()
        self.assert_ok(self.command("--check"))
        self.assert_ok(self.command())
        self.assertEqual(path.read_bytes(), formatted)

    def test_base_uses_common_ancestor_and_includes_local_changes(self):
        self.git("checkout", "-qb", "feature")
        self.write("unstaged.c", CHANGED)
        self.git("add", "unstaged.c")
        self.git("commit", "-qm", "Feature change")
        self.git("checkout", "-q", "main")
        self.write("untouched.c", "int target_only(){return 2;}\n")
        self.git("add", "untouched.c")
        self.git("commit", "-qm", "Target branch change")
        self.git("checkout", "-q", "feature")
        self.assert_ok(self.command())
        self.assertEqual((self.repo / "unstaged.c").read_text(), CHANGED)
        self.write("local.h", CHANGED)
        result = self.command("--base", "main")
        self.assert_ok(result)
        self.assertIn("Selected 2 changed", result.stdout)
        self.assertIn("\n{\n", (self.repo / "unstaged.c").read_text())
        self.assertIn("\n{\n", (self.repo / "local.h").read_text())
        self.assertEqual((self.repo / "untouched.c").read_text(), ORIGINAL)

    def test_missing_style_stops_before_any_file_changes(self):
        (self.repo / ".clang-format").unlink()
        self.write("configured/.clang-format", STYLE)
        configured = self.write("configured/a.c", CHANGED)
        missing = self.write("unstaged.c", CHANGED)
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No .clang-format or _clang-format", result.stderr)
        self.assertEqual(configured.read_text(), CHANGED)
        self.assertEqual(missing.read_text(), CHANGED)

    def test_nearest_underscore_configuration(self):
        self.write("nested/_clang-format", STYLE.replace("IndentWidth: 4", "IndentWidth: 8"))
        path = self.write("nested/new.c", CHANGED)
        self.assert_ok(self.command())
        self.assertIn("\n        int value = 1;", path.read_text())

    def test_invalid_base_does_not_format(self):
        path = self.write("unstaged.c", CHANGED)
        result = self.command("--base", "missing-reference")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), CHANGED)

    def test_conflicts_do_not_format(self):
        self.git("checkout", "-qb", "feature")
        self.write("unstaged.c", CHANGED)
        self.git("add", "unstaged.c")
        self.git("commit", "-qm", "Feature edit")
        self.git("checkout", "-q", "main")
        self.write("unstaged.c", "int conflict(){return 2;}\n")
        self.git("add", "unstaged.c")
        self.git("commit", "-qm", "Other edit")
        self.assertNotEqual(self.git("merge", "feature", check=False).returncode, 0)
        before = (self.repo / "unstaged.c").read_bytes()
        other = self.write("new.c", CHANGED)
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Resolve merge conflicts", result.stderr)
        self.assertEqual((self.repo / "unstaged.c").read_bytes(), before)
        self.assertEqual(other.read_text(), CHANGED)

    def test_git_symlink_placeholder_is_excluded(self):
        self.git("config", "core.symlinks", "false")
        oid = self.git("hash-object", "-w", "--stdin", input=b"untouched.c").stdout.strip().decode()
        self.git("update-index", "--add", "--cacheinfo", "120000", oid, "link.c")
        self.git("checkout-index", "-f", "--", "link.c")
        result = self.command()
        self.assert_ok(result)
        self.assertIn("No changed C/C++", result.stdout)
        self.assertEqual((self.repo / "link.c").read_bytes(), b"untouched.c")

    def test_submodule_changes_are_excluded(self):
        with tempfile.TemporaryDirectory(prefix="env submodule ") as directory:
            source = Path(directory)
            subprocess.run([str(GIT), "clone", "-q", "--template=", str(self.repo), str(source)],
                           env=self.env, check=True)
            self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "vendor")
            self.git("commit", "-qm", "Add submodule")
            path = self.write("vendor/unstaged.c", CHANGED)
            result = self.command()
            self.assert_ok(result)
            self.assertIn("No changed C/C++", result.stdout)
            self.assertEqual(path.read_text(), CHANGED)

    def test_invalid_configuration_returns_error(self):
        self.write(".clang-format", "IndentWidth: not-a-number\n")
        path = self.write("unstaged.c", CHANGED)
        result = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error reading", result.stderr)
        self.assertEqual(path.read_text(), CHANGED)

    def test_clean_repository_is_noop(self):
        result = self.command()
        self.assert_ok(result)
        self.assertIn("No changed C/C++", result.stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, b"")

    def test_initial_repository_without_commits(self):
        fresh = self.repo / "fresh"
        fresh.mkdir()
        subprocess.run([str(GIT), "init", "-q", "--template="], cwd=fresh,
                       env=self.env, check=True)
        (fresh / ".clang-format").write_text(STYLE)
        path = fresh / "first.c"
        path.write_text(CHANGED)
        subprocess.run([str(GIT), "add", "first.c"], cwd=fresh, env=self.env, check=True)
        self.assert_ok(self.command(cwd=fresh))
        self.assertIn("\n{\n", path.read_text())

    def test_outside_repository_reports_error(self):
        with tempfile.TemporaryDirectory(prefix="env not a repo ") as directory:
            result = self.command(cwd=directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
