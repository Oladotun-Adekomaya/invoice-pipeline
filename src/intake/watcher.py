import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from src.config import settings
from src.intake.file_handler import stage
from src.observability.logger import get_logger, setup_logging

logger = get_logger(__name__)


class InvoiceHandler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        path = Path(str(event.src_path))

        if path.suffix.lower() != ".pdf":
            return

        logger.info("new file detected", filename=path.name)

        try:
            record = stage(path)
            logger.info(
                "file ready for pipeline",
                file_id=str(record.file_id),
                filename=record.original_filename,
            )
        except Exception as e:
            logger.error(
                "staging failed",
                filename=path.name,
                error=str(e),
            )


def start_watcher() -> None:
    watch_dir = Path(settings.intake_watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)

    handler = InvoiceHandler()
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()

    logger.info("watcher started", watching=str(watch_dir))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logger.info("watcher stopped")

    observer.join()


if __name__ == "__main__":
    setup_logging()
    start_watcher()