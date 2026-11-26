import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def write_private_json(destination: Path, payload: bytes, *, overwrite: bool) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload + b"\n")
            temporary.flush()
            os.fsync(temporary.fileno())

        if overwrite:
            temporary_path.replace(destination)
            temporary_path = None
        elif os.name == "nt":
            # Windows rename fails if the destination exists.
            temporary_path.rename(destination)
            temporary_path = None
        else:
            os.link(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
