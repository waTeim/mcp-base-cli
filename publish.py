#!/usr/bin/env python3
"""
Publish mcp-base to PyPI.

Usage:
    python publish.py          # Build and publish to Test PyPI (default)
    python publish.py --prod   # Build and publish to production PyPI
    python publish.py --build  # Build only, don't publish
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def main():
    parser = argparse.ArgumentParser(
        description="Build and publish mcp-base to PyPI"
    )
    parser.add_argument(
        "--prod", "--production",
        action="store_true",
        help="Publish to production PyPI (default: Test PyPI)"
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build only, don't publish"
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent.resolve()

    # Clean previous builds
    print("\n🧹 Cleaning previous builds...")
    for path in ["dist", "build"]:
        full_path = script_dir / path
        if full_path.exists():
            shutil.rmtree(full_path)
    for egg_info in script_dir.glob("src/*.egg-info"):
        shutil.rmtree(egg_info)

    # Ensure build tools are installed
    print("\n📦 Checking build tools...")
    run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "build", "twine"])

    # Build the package
    print("\n🔨 Building package...")
    run([sys.executable, "-m", "build"], check=True)

    # Show what was built
    print("\n✅ Built packages:")
    for f in (script_dir / "dist").iterdir():
        print(f"   {f.name}")

    if args.build:
        print("\n✅ Build complete. Packages are in dist/")
        return

    # Confirm before publishing to production
    if args.prod:
        print("\n⚠️  WARNING: You are about to publish to PRODUCTION PyPI!")
        confirm = input("Are you sure? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(1)
        repository = "pypi"
    else:
        repository = "testpypi"

    # Publish
    print(f"\n🚀 Publishing to {repository}...")
    if repository == "testpypi":
        run([sys.executable, "-m", "twine", "upload", "--repository", "testpypi", "dist/*"])
        print("\n✅ Published to Test PyPI!")
        print("\nTo install from Test PyPI:")
        print("  pip install --index-url https://test.pypi.org/simple/ \\")
        print("      --extra-index-url https://pypi.org/simple/ mcp-base")
    else:
        run([sys.executable, "-m", "twine", "upload", "dist/*"])
        print("\n✅ Published to PyPI!")
        print("\nTo install:")
        print("  pip install mcp-base")


if __name__ == "__main__":
    main()
