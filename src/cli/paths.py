"""Virtual path handling rooted at ``data/main``."""

from pathlib import Path


class VirtualFileSystem:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cwd = self.root

    def resolve(self, value: str = ".") -> Path:
        """Resolve a CLI path and reject paths (including symlinks) outside main."""
        if value in ("", "~"):
            candidate = self.root
        elif value.startswith("~/"):
            candidate = self.root / value[2:]
        elif value.startswith("/"):
            # Absolute paths in the CLI are virtual paths, not host paths.
            candidate = self.root / value.lstrip("/")
        else:
            candidate = self.cwd / value

        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径超出虚拟根目录: {value}") from exc
        return candidate

    def display(self, path: Path | None = None) -> str:
        path = (path or self.cwd).resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return "/"
        return "/" if str(relative) == "." else f"/{relative.as_posix()}"

    def completion_candidates(self, value: str, directories_only: bool = False) -> list[str]:
        """Return virtual path candidates for readline completion."""
        expanded = value
        if expanded == "~":
            lookup_parent, render_prefix, leaf = self.root, "~/", ""
        elif expanded.startswith("~/"):
            parent_text, leaf = expanded.rsplit("/", 1)
            parent_text = parent_text or "~"
            lookup_parent = self.resolve(parent_text)
            render_prefix = parent_text + "/"
        elif "/" in expanded:
            parent_text, leaf = expanded.rsplit("/", 1)
            if not parent_text:
                parent_text = "/"
                render_prefix = "/"
            else:
                render_prefix = parent_text.rstrip("/") + "/"
            lookup_parent = self.resolve(parent_text)
        else:
            leaf = expanded
            render_prefix = ""
            lookup_parent = self.cwd

        try:
            entries = sorted(lookup_parent.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except (OSError, ValueError):
            return []

        candidates: list[str] = []
        for special in (".", "..", "~"):
            if special.startswith(value):
                candidates.append(special)
        for entry in entries:
            if entry.name == ".folder" or not entry.name.startswith(leaf):
                continue
            try:
                entry.resolve().relative_to(self.root)
            except (OSError, ValueError):
                continue
            is_dir = entry.is_dir()
            if directories_only and not is_dir:
                continue
            candidates.append(f"{render_prefix}{entry.name}{'/' if is_dir else ''}")
        return candidates
