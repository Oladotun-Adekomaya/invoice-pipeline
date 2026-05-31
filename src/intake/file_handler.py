import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from src.observability.logger import get_logger

logger = get_logger(__name__)

STAGING_DIR = Path("./staging")


@dataclass
class FileRecord:
    file_id: UUID
    original_filename: str
    staged_path: Path
    sha256: str
    received_at: datetime
    source: str


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def stage(path: Path, source: str = "local_drop") -> FileRecord:
    file_id = uuid4()
    dest_dir = STAGING_DIR / str(file_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / path.name
    shutil.copy2(path, dest_path)

    sha256 = compute_sha256(dest_path)

    record = FileRecord(
        file_id=file_id,
        original_filename=path.name,
        staged_path=dest_path,
        sha256=sha256,
        received_at=datetime.now(timezone.utc),
        source=source,
    )

    logger.info(
        "file staged",
        file_id=str(file_id),
        filename=path.name,
        sha256=sha256[:12],
        source=source,
    )

    return record