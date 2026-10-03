"""Stage only application source and build manifests for Supabase Compute."""

import shutil
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "supabase" / "compute" / "threadsong"
    target.mkdir(parents=True, exist_ok=True)
    for filename in ("Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock"):
        shutil.copy2(root / filename, target / filename)
    shutil.copytree(
        root / "src",
        target / "src",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"),
    )
    print(f"Prepared Compute build context: {target}")


if __name__ == "__main__":
    main()
