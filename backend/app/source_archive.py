"""Keep original inputs available to saved comparisons and exports."""

from pathlib import Path
from uuid import UUID

from .private_json_storage import write_private_json


def archive_marker(directory: Path, source_id: UUID) -> Path:
    return directory / f"{source_id}.deleted.json"


def is_archived(directory: Path, source_id: UUID) -> bool:
    return archive_marker(directory, source_id).is_file()


def archive_source(directory: Path, source_id: UUID) -> None:
    try:
        write_private_json(archive_marker(directory, source_id), b"{}", overwrite=False)
    except FileExistsError:
        pass  # Concurrent and repeated requests publish the same marker.
