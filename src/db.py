import psycopg
from pathlib import Path
from src.config import settings
from src.observability.logger import get_logger

logger = get_logger(__name__)


def run_migrations() -> None:
    sql = Path("migrations/001_initial_schema.sql").read_text()
    logger.info("running migrations")
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type]
        conn.commit()
    logger.info("migrations complete")