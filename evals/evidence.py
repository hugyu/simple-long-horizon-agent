"""Capture local experiment source without credentials or generated outputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tarfile


def capture_source(output: Path) -> dict[str, str]:
    """Archive runnable source and the dependency lock for exact later inspection."""
    root = Path(__file__).resolve().parents[1]
    files = [root / "pyproject.toml", root / "uv.lock"]
    for directory in ("src", "scripts", "runs", "evals", "examples", "tests"):
        files.extend(
            path
            for path in (root / directory).rglob("*")
            if path.suffix in {".py", ".sh"}
            and path.is_file()
            and not path.is_relative_to(root / "evals" / "out")
        )
    archive = output / "source.tar.gz"
    digest = hashlib.sha256()
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(files):
            relative = str(path.relative_to(root))
            digest.update(relative.encode())
            digest.update(path.read_bytes())
            bundle.add(path, arcname=relative)
    return {
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "source_tree_sha256": digest.hexdigest(),
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
