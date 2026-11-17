"""Audit published build inputs; wheel presence is not runtime certification."""

import json
from urllib.request import urlopen

PLATFORMS = {
    "windows-x86_64": ("win_amd64",),
    "windows-arm64": ("win_arm64",),
    "macos-x86_64": ("x86_64", "universal2"),
    "macos-arm64": ("arm64", "universal2"),
    "linux-x86_64": ("manylinux2014_x86_64",),
    "linux-arm64": ("manylinux2014_aarch64",),
}


def main():
    result = {}
    for package, version in (("imageio-ffmpeg", "0.6.0"), ("pyinstaller", "6.22.3")):
        with urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=20) as response:
            metadata = json.load(response)
        wheels = [
            item["filename"] for item in metadata["urls"] if item["packagetype"] == "bdist_wheel"
        ]
        coverage = {}
        for platform, tags in PLATFORMS.items():
            os_name = platform.split("-")[0]
            coverage[platform] = [
                name
                for name in wheels
                if any(tag in name for tag in tags) and (os_name != "macos" or "macosx" in name)
            ]
        result[package] = {"version": version, "published_wheels": coverage}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
