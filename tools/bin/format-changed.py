"""Format changed C/C++ files with Env's bundled clang-format."""

import argparse
import os
from pathlib import Path
import subprocess
import sys


BIN_DIR = Path(__file__).resolve().parent
GIT = BIN_DIR.parent / "git-2.41.0-32-bit" / "cmd" / "git.exe"
CLANG_FORMAT = BIN_DIR / "clang-format.exe"
EXTENSIONS = {".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".cxx", ".hxx",
              ".c++", ".h++", ".inl", ".ipp", ".tpp"}
DIFF_OPTIONS = ["--name-only", "-z", "--diff-filter=ACMR",
                "--no-ext-diff", "--ignore-submodules=all"]


class CommandError(Exception):
    pass


def git(*args, cwd=None):
    result = subprocess.run(
        [str(GIT), *args], cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise CommandError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def filenames(data):
    return {os.fsdecode(name) for name in data.split(b"\0") if name}


def changed_files(root, base):
    symlinks = set()
    for entry in git("ls-files", "--stage", "-z", cwd=root).split(b"\0"):
        if not entry:
            continue
        metadata, _, name = entry.partition(b"\t")
        mode, _, stage = metadata.split()
        if stage != b"0":
            raise CommandError("Resolve merge conflicts before running format-changed.")
        if mode == b"120000":
            symlinks.add(os.fsdecode(name))

    names = filenames(git("diff", *DIFF_OPTIONS, "--", cwd=root))
    names.update(filenames(git("diff", "--cached", *DIFF_OPTIONS, "--", cwd=root)))
    names.update(filenames(git("ls-files", "--others", "--exclude-standard", "-z", cwd=root)))

    if base:
        revision = git("rev-parse", "--verify", "--end-of-options",
                       base + "^{commit}", cwd=root).strip().decode("ascii")
        ancestor = git("merge-base", "HEAD", revision, cwd=root).strip().decode("ascii")
        names.update(filenames(git("diff", *DIFF_OPTIONS, ancestor, "HEAD", "--", cwd=root)))

    selected = []
    for name in sorted(names - symlinks):
        path = root / name
        if (path.suffix.lower() in EXTENSIONS and path.is_file()
                and not path.is_symlink() and path.resolve().is_relative_to(root)):
            selected.append(path)
    return selected


def has_project_style(path, root):
    directory = path.parent
    while True:
        if any((directory / name).is_file() for name in (".clang-format", "_clang-format")):
            return True
        if directory == root:
            return False
        directory = directory.parent


def main():
    parser = argparse.ArgumentParser(
        prog="format-changed",
        description=("Format whole changed C/C++ files with clang-format 22.1.8. "
                     "Includes unstaged, staged and untracked files; leaves the Git index unchanged."),
    )
    parser.add_argument("--base", metavar="REF",
                        help="also include committed changes since the merge base of HEAD and REF")
    parser.add_argument("--check", action="store_true",
                        help="check formatting without modifying files (nonzero exit on violations)")
    args = parser.parse_args()

    try:
        if not CLANG_FORMAT.is_file():
            raise CommandError("Env's bundled clang-format.exe was not found.")
        root = Path(os.fsdecode(git("rev-parse", "--show-toplevel").rstrip(b"\r\n"))).resolve()
        files = changed_files(root, args.base)
        if not files:
            print("No changed C/C++ files to process.")
            return 0

        missing = [path.relative_to(root).as_posix() for path in files
                   if not has_project_style(path, root)]
        if missing:
            raise CommandError(
                "No .clang-format or _clang-format found inside the repository for:\n  "
                + "\n  ".join(missing)
                + "\nAdd the project's formatting configuration and run again. No files were formatted."
            )

        options = ["--style=file", "--fallback-style=none", "--Werror"]
        options += ["--dry-run"] if args.check else ["-i"]
        action = "Checking" if args.check else "Formatting"
        print(f"Selected {len(files)} changed C/C++ file(s).", flush=True)
        failed = False
        for path in files:
            print(f"{action}: {path.relative_to(root).as_posix()}", flush=True)
            result = subprocess.run([str(CLANG_FORMAT), *options, str(path)], cwd=root)
            if result.returncode:
                failed = True
                if not args.check:
                    return result.returncode
        if failed:
            return 1
        if args.check:
            print("Formatting check passed.")
        else:
            print("Done. Review git diff and git add the files you want to commit.")
        return 0
    except (CommandError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
