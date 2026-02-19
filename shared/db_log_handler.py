import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db_models import AppLog

DATABASE_URL = os.getenv("DATABASE_URL")

_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

class DBLogHandler(logging.Handler):
    """
    Stores JSON log events into Postgres.
    Designed for moderate volume (admin visibility).
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            # structlog JSON is already in msg; parse if you want. We'll store raw + metadata.
            service = getattr(record, "service", None)

            # Try to extract fields if record has them (structlog can attach extras)
            request_id = getattr(record, "request_id", None)
            task_id = getattr(record, "task_id", None)

            db = SessionLocal()
            db.add(
                AppLog(
                    level=record.levelname,
                    logger=record.name,
                    service=service,
                    message=msg,
                    request_id=request_id,
                    task_id=task_id,
                    event=getattr(record, "event", None),
                    data=getattr(record, "data", None),
                )
            )
            db.commit()
        except Exception:
            # never crash the app for logging
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass
