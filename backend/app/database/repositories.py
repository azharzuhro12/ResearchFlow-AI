"""Repositories — the only layer that talks to the ORM (Step 9).

Each repository method is one small unit of work on a caller-provided
Session; the caller owns the transaction via ``database.session_scope``
(commit on success, rollback on error). Every statement goes through
SQLAlchemy's parameterized expression API — there is no string-built SQL
anywhere in the codebase.

Datetime convention: write methods accept timezone-aware UTC datetimes
and normalize them for storage; read methods return ORM rows whose naive
UTC values mappers convert with ``database.from_db``.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.database.database import to_db
from app.database.models import ExecutionRow, ReportRow, ScheduleRow


class ScheduleRepository:
    """Persistence for scheduled research jobs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        schedule_id: str,
        question: str,
        schedule_type: str,
        interval_minutes: int | None,
        cron_expression: str | None,
        timezone: str,
        enabled: bool,
        created_at: datetime,
        next_run_at: datetime | None,
        last_status: str,
        notification_status: str,
    ) -> None:
        self._session.add(
            ScheduleRow(
                id=schedule_id,
                question=question,
                schedule_type=schedule_type,
                interval_minutes=interval_minutes,
                cron_expression=cron_expression,
                timezone=timezone,
                enabled=enabled,
                created_at=to_db(created_at),
                updated_at=to_db(created_at),
                next_run_at=to_db(next_run_at),
                last_status=last_status,
                notification_status=notification_status,
            )
        )

    def get(self, schedule_id: str) -> ScheduleRow | None:
        return self._session.get(ScheduleRow, schedule_id)

    def list_all(self) -> Sequence[ScheduleRow]:
        """All schedules, oldest first (stable order for restore + UI)."""
        return self._session.scalars(
            select(ScheduleRow).order_by(ScheduleRow.created_at, ScheduleRow.id)
        ).all()

    def count(self) -> int:
        return (
            self._session.scalar(
                select(func.count()).select_from(ScheduleRow)
            )
            or 0
        )

    def set_enabled(
        self,
        schedule_id: str,
        *,
        enabled: bool,
        next_run_at: datetime | None,
        updated_at: datetime,
    ) -> int:
        """Persist a pause/resume. Returns affected row count."""
        result = self._session.execute(
            update(ScheduleRow)
            .where(ScheduleRow.id == schedule_id)
            .values(
                enabled=enabled,
                next_run_at=to_db(next_run_at),
                updated_at=to_db(updated_at),
            )
        )
        return result.rowcount or 0

    def mark_started(
        self,
        schedule_id: str,
        *,
        last_run_at: datetime,
        last_status: str,
        updated_at: datetime,
    ) -> None:
        self._session.execute(
            update(ScheduleRow)
            .where(ScheduleRow.id == schedule_id)
            .values(
                last_run_at=to_db(last_run_at),
                last_status=last_status,
                updated_at=to_db(updated_at),
            )
        )

    def record_outcome(
        self,
        schedule_id: str,
        *,
        last_status: str,
        last_error: str | None,
        last_report_id: str | None,
        next_run_at: datetime | None,
        updated_at: datetime,
    ) -> None:
        self._session.execute(
            update(ScheduleRow)
            .where(ScheduleRow.id == schedule_id)
            .values(
                last_status=last_status,
                last_error=last_error,
                last_report_id=last_report_id,
                next_run_at=to_db(next_run_at),
                updated_at=to_db(updated_at),
            )
        )

    def set_notification_status(
        self, schedule_id: str, status: str, *, updated_at: datetime
    ) -> None:
        self._session.execute(
            update(ScheduleRow)
            .where(ScheduleRow.id == schedule_id)
            .values(notification_status=status, updated_at=to_db(updated_at))
        )

    def delete(self, schedule_id: str) -> None:
        """Delete the schedule row; history rows survive via SET NULL."""
        self._session.execute(
            delete(ScheduleRow).where(ScheduleRow.id == schedule_id)
        )


class ExecutionRepository:
    """Persistence for research pipeline executions (run history)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        execution_id: str,
        schedule_id: str,
        trigger_type: str,
        started_at: datetime,
        notification_status: str,
    ) -> None:
        self._session.add(
            ExecutionRow(
                id=execution_id,
                schedule_id=schedule_id,
                trigger_type=trigger_type,
                started_at=to_db(started_at),
                status="running",
                notification_status=notification_status,
            )
        )

    def get(self, execution_id: str) -> ExecutionRow | None:
        return self._session.get(ExecutionRow, execution_id)

    def mark_completed(
        self,
        execution_id: str,
        *,
        status: str,
        completed_at: datetime,
        error: str | None,
        report_id: str | None,
        source_count: int | None,
    ) -> int:
        """Record the run outcome. Returns affected row count."""
        result = self._session.execute(
            update(ExecutionRow)
            .where(ExecutionRow.id == execution_id)
            .values(
                status=status,
                completed_at=to_db(completed_at),
                error=error,
                report_id=report_id,
                source_count=source_count,
            )
        )
        return result.rowcount or 0

    def set_notification_status(self, execution_id: str, status: str) -> None:
        self._session.execute(
            update(ExecutionRow)
            .where(ExecutionRow.id == execution_id)
            .values(notification_status=status)
        )

    def list_for_schedule(
        self, schedule_id: str, *, limit: int
    ) -> Sequence[ExecutionRow]:
        """Most recent executions for one schedule (newest first)."""
        return self._session.scalars(
            select(ExecutionRow)
            .where(ExecutionRow.schedule_id == schedule_id)
            .order_by(ExecutionRow.started_at.desc(), ExecutionRow.id.desc())
            .limit(limit)
        ).all()

    def count_for_schedule(self, schedule_id: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(ExecutionRow)
                .where(ExecutionRow.schedule_id == schedule_id)
            )
            or 0
        )


class ReportRepository:
    """Persistence for generated report METADATA (files stay in reports/)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        report_id: str,
        schedule_id: str | None,
        execution_id: str | None,
        query: str,
        markdown_filename: str,
        pdf_filename: str,
        created_at: datetime,
        synthesis_status: str,
    ) -> None:
        if self._session.get(ReportRow, report_id) is not None:
            return  # idempotent: never duplicate metadata for one report
        self._session.add(
            ReportRow(
                id=report_id,
                schedule_id=schedule_id,
                execution_id=execution_id,
                query=query,
                markdown_filename=markdown_filename,
                pdf_filename=pdf_filename,
                created_at=to_db(created_at),
                synthesis_status=synthesis_status,
            )
        )

    def get(self, report_id: str) -> ReportRow | None:
        return self._session.get(ReportRow, report_id)

    def list_recent(self, *, limit: int) -> Sequence[ReportRow]:
        """Most recently generated reports (newest first)."""
        return self._session.scalars(
            select(ReportRow)
            .order_by(ReportRow.created_at.desc(), ReportRow.id.desc())
            .limit(limit)
        ).all()

    def count(self) -> int:
        return (
            self._session.scalar(select(func.count()).select_from(ReportRow))
            or 0
        )
