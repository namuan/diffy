from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
import tomllib


def build() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    with (root_dir / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file)
    pyinstaller_config = config["tool"]["pyinstaller"]
    app_name = pyinstaller_config["app_name"]
    entry_point = root_dir / pyinstaller_config["entry_point"]
    icon_path = root_dir / pyinstaller_config["icon_path"]
    bundle_id = pyinstaller_config["bundle_id"]
    for directory in (root_dir / "dist", root_dir / "build"):
        if directory.exists():
            shutil.rmtree(directory)
    command = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        f"--name={app_name}",
        f"--osx-bundle-identifier={bundle_id}",
        "--target-architecture=arm64",
        str(entry_point),
    ]
    if icon_path.exists():
        command.append(f"--icon={icon_path}")
    subprocess.run(command, check=True, cwd=root_dir)
    print(f"Build complete: dist/{app_name}.app")


if __name__ == "__main__":
    build()
