"""Database backup service — daily SQLite backups with rotation."""

import sqlite3
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger()


class BackupService:
    """Creates atomic SQLite backups and rotates old ones."""

    def __init__(
        self,
        db_path: str = "data/agent_team.db",
        backup_dir: str = "backups",
        max_backups: int = 30,
    ) -> None:
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.max_backups = max_backups

    def backup(self) -> Path | None:
        if not self.db_path.exists():
            logger.warning("backup_skipped_no_db", db_path=str(self.db_path))
            return None

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"agent_team_{timestamp}.db"

        source = sqlite3.connect(str(self.db_path))
        dest = sqlite3.connect(str(backup_path))
        try:
            source.backup(dest)
            logger.info("backup_created", path=str(backup_path))
        finally:
            dest.close()
            source.close()

        self._rotate()
        return backup_path

    def restore(self, backup_name: str) -> bool:
        backup_path = self.backup_dir / backup_name
        if not backup_path.exists():
            logger.error("restore_failed_not_found", path=str(backup_path))
            return False

        source = sqlite3.connect(str(backup_path))
        dest = sqlite3.connect(str(self.db_path))
        try:
            source.backup(dest)
            logger.info("backup_restored", from_path=str(backup_path))
        finally:
            dest.close()
            source.close()
        return True

    def list_backups(self) -> list[str]:
        if not self.backup_dir.exists():
            return []
        return sorted(
            [f.name for f in self.backup_dir.glob("agent_team_*.db")],
            reverse=True,
        )

    def _rotate(self) -> None:
        backups = sorted(self.backup_dir.glob("agent_team_*.db"))
        while len(backups) > self.max_backups:
            old = backups.pop(0)
            old.unlink()
            logger.info("backup_rotated", removed=old.name)
