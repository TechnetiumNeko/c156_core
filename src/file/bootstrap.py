"""Create and synchronize the initial data folder object tree."""

from __future__ import annotations

from pathlib import Path

from .folder import Folder


ROOT_FOLDERS = ("main", "admin", "resource", "bin")


def initialize_data(data_root: str | Path) -> Folder:
    """Create a ``.folder`` SQLite container inside each real data directory.

    The data directory is the logical root (its parent_id is NULL). Existing
    directory IDs are preserved, and each folder's file table is synchronized
    with its immediate child directories.
    """
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in ROOT_FOLDERS:
        (root / name).mkdir(exist_ok=True)

    directories = [root]
    directories.extend(path for path in root.rglob("*") if path.is_dir())
    directories.sort(key=lambda path: (len(path.relative_to(root).parts), path.as_posix()))

    folders: dict[Path, Folder] = {}
    for directory in directories:
        relative = directory.relative_to(root)
        fullpath = "/data" if not relative.parts else f"/data/{relative.as_posix()}"
        parent_id = None if not relative.parts else folders[directory.parent].id
        container = directory / ".folder"

        if container.exists():
            folder = Folder(container)
            if folder.fullpath != fullpath:
                raise ValueError(
                    f".folder 路径不匹配: {container} 保存为 {folder.fullpath}，预期 {fullpath}"
                )
            if folder.parent_id != parent_id:
                raise ValueError(f".folder 逻辑上级不匹配: {container}")
        else:
            folder = Folder.create(container, fullpath=fullpath, parent_id=parent_id)
        folders[directory] = folder

    for directory in directories:
        child_directories = sorted(
            (path for path in directory.iterdir() if path.is_dir()),
            key=lambda path: (path.name.casefold(), path.name),
        )
        folders[directory].set_children([folders[child].id for child in child_directories])

    return folders[root]


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]
    root = initialize_data(repository_root / "data")
    print(f"已初始化 data 文件夹对象: {root.fullpath} (id={root.id}, parent_id={root.parent_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
