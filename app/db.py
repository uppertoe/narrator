"""SQLite engine + session helpers."""
from __future__ import annotations

import os
from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

# Override in production (e.g. sqlite:////data/narrator.db on a writable volume).
DATABASE_URL = os.environ.get("NARRATOR_DATABASE_URL", "sqlite:///narrator.db")

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    # Import models so their tables register on SQLModel.metadata.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
