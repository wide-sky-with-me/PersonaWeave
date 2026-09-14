"""Private artifact staging shared by session and legacy record deletion."""

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def staged_deletion(trash_root: Path):
    """Yield a move function; restore staged paths if the enclosed DB commit fails.

    Callers hold their record lock and supply only direct, server-derived children
    of managed roots. Symlinks themselves are moved, never their external targets.
    Committed trash is private and cleanup is best-effort, like batch deletion.
    """
    directory = trash_root / uuid4().hex
    moved = []

    def stage(paths):
        for root, path in paths:
            if path.parent.resolve() != root.resolve() or path.name in {"", ".", ".."}:
                raise ValueError("Deletion path is outside its managed root")
            if not path.exists() and not path.is_symlink():
                continue
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / str(len(moved))
            os.replace(path, destination)
            moved.append((path, destination))

    try:
        yield stage
    except BaseException:
        for path, destination in reversed(moved):
            os.replace(destination, path)
        raise
    finally:
        # Only remove empty trash on rollback. If restore itself fails, retain
        # the remaining private bytes for recovery rather than destroying them.
        if not any(destination.exists() or destination.is_symlink() for _, destination in moved):
            shutil.rmtree(directory, ignore_errors=True)
    shutil.rmtree(directory, ignore_errors=True)
