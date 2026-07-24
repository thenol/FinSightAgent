import hashlib
from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def put(self, content: bytes) -> tuple[str, str]: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        self.objects.setdefault(digest, content)
        return digest, f"memory://artifacts/{digest}"


class LocalArtifactStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def put(self, content: bytes) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest[:2] / digest[2:4] / digest
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        return digest, str(path.resolve())
