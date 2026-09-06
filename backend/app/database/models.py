"""SQLAlchemy ORM models — SQLite persistence layer (Step 9).

Three tables hold all durable state:

- ``schedules`` — one row per scheduled research job. This is the source
  of truth the scheduler restores from on startup.
- ``research_executions`` — one row per pipeline run (manual or
  scheduled), written at run start and updated at completion.
- ``reports`` — METADATA for every generated report pair. The Markdown
  and PDF files themselves stay in ``reports/``; only identifiers and
  filenames are stored here.

Conventions:
- Datetimes are stored as naive UTC (SQLite has no real timezone type);
  the database helpers ``to_db`` / ``from_db`` convert at the boundary.
- Deleting a schedule PRESERVES history: execution/report foreign keys
  are nullable with ON DELETE SET NULL, and report files are never
  deleted automatically.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ResearchFlow tables."""


class ScheduleRow(Base):
    """A scheduled research job (persisted scheduler registry entry)."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    question: Mapped[str] = mapped_column(String(2000))
    schedule_type: Mapped[str] = mapped_column(String(10))
    interval_minutes: Mapped[int | None] = mapped_column(default=None)
    cron_expression: Mapped[str | None] = mapped_column(String(100), default=None)
    timezone: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    last_status: Mapped[str] = mapped_column(String(20), default="never_run")
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    last_report_id: Mapped[str | None] = mapped_column(String(100), default=None)
    notification_status: Mapped[str] = mapped_column(
        String(20), default="not_configured"
    )

    executions: Mapped[list[ExecutionRow]] = relationship(
        back_populates="schedule", passive_deletes=True
    )
    reports: Mapped[list[ReportRow]] = relationship(
        back_populates="schedule", passive_deletes=True
    )


class ExecutionRow(Base):
    """One research pipeline run (scheduled or manual).

    `error` is always the client-safe message — never a stack trace, API
    key, header, or environment value.
    """

    __tablename__ = "research_executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Nullable + SET NULL: deleting a schedule preserves history rows.
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True, default=None
    )
    trigger_type: Mapped[str] = mapped_column(String(10))  # scheduled|manual
    started_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), default=None)
    status: Mapped[str] = mapped_column(String(10))  # running|success|failed
    error: Mapped[str | None] = mapped_column(String(500), default=None)
    report_id: Mapped[str | None] = mapped_column(String(100), default=None)
    source_count: Mapped[int | None] = mapped_column(default=None)
    notification_status: Mapped[str] = mapped_column(
        String(20), default="not_configured"
    )

    schedule: Mapped[ScheduleRow | None] = relationship(
        back_populates="executions"
    )


class ReportRow(Base):
    """Metadata for one generated Markdown + PDF report pair.

    The report FILES live in ``reports/``; this row only records what was
    generated, by which run, and under which filenames. Rows are never
    deleted automatically (no cleanup job by design).
    """

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True, default=None
    )
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_executions.id", ondelete="SET NULL"),
        index=True,
        default=None,
    )
    query: Mapped[str] = mapped_column(String(2000))
    markdown_filename: Mapped[str] = mapped_column(String(120))
    pdf_filename: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    synthesis_status: Mapped[str] = mapped_column(String(30))

    schedule: Mapped[ScheduleRow | None] = relationship(back_populates="reports")
