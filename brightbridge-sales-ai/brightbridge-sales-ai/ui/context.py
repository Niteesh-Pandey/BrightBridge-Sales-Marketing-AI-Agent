"""Application context: active data mode + database. One context per running app (single-tenant MVP)."""
from __future__ import annotations

import config
from database.db import Database, DatabaseError
from database.seed import ensure_demo_data
from services import crm_service


class AppContext:
    def __init__(self, mode: str | None = None):
        self.mode = mode or config.DATA_MODE
        self.db: Database | None = None
        self.error: str | None = None
        self.activate(self.mode)

    def activate(self, mode: str) -> None:
        """Switch between demo and real data. Demo data is generated on first use."""
        self.mode, self.error = mode, None
        try:
            self.db = Database(config.default_database_url(mode))
            self.db.initialize()
            if mode == "demo":
                ensure_demo_data(self.db)
        except Exception as exc:  # never show raw traces
            self.db = None
            self.error = f"Database unavailable ({type(exc).__name__}). Check DATABASE_URL and file permissions."

    def regenerate_demo(self) -> str:
        from database.seed import seed_database
        from scripts.generate_demo_data import generate
        if self.mode != "demo" or self.db is None:
            return "Demo data can only be regenerated in Demo mode."
        seed_database(self.db, generate())
        return "Demo data regenerated."

    def require_db(self) -> Database:
        if self.db is None:
            raise DatabaseError(self.error or "Database unavailable.")
        return self.db

    def frame(self):
        return crm_service.get_lead_frame(self.require_db())

    @property
    def banner(self) -> str:
        return config.DEMO_BANNER if self.mode == "demo" else "REAL DATA MODE — Your imported business data"
