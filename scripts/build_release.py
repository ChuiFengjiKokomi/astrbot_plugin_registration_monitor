"""生成根目录可直接安装、也可解压后发布到 GitHub 的源码 ZIP。"""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "main.py",
    "monitor.py",
    "ranking.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    "LICENSE",
    ".gitignore",
    "ruff.toml",
    ".github/workflows/tests.yml",
    "scripts/build_release.py",
)


def build_release() -> Path:
    files = [ROOT / name for name in FILES]
    files.extend(sorted((ROOT / "tests").glob("*.py")))
    missing = [str(path.relative_to(ROOT)) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing release files: " + ", ".join(missing))
    destination = ROOT / "dist" / "astrbot_plugin_registration_monitor.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 明确列出源文件，避免将缓存、虚拟环境或本地运行数据打包。
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    return destination


if __name__ == "__main__":
    print(build_release())
