"""Tests for the SQLite persistence layer (Step 9) — engine, helpers,
session management, and all three repositories.

Every test runs against its own temporary SQLite file (see
conftest.temporary_database); the production data/researchflow.db is
never touched.
"""

from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core import config
from app.database.database import (
    PersistenceError,
    from_db,
    get_engine,
    init_database,
    reset_engine_for_tests,
    session_scope,
    to_db,
)
from app.database.repositories import (
    ExecutionRepository,
    ReportRepository,
    ScheduleRepository,
)


def utc(moment: datetime | None = None) -> datetime:
    return moment or datetime.now(UTC)


# ------------------------------------------------------------ bootstrap


def test_init_database_creates_all_three_tables(temporary_database: Path) -> None:
    init_database()
    tables = set(inspect(get_engine()).get_table_names())
    assert {"schedules", "research_executions", "reports"} <= tables


def test_init_database_is_idempotent_and_preserves_rows(
    temporary_database: Path,
) -> None:
    init_database()
    with session_scope() as session:
        ScheduleRepository(session).create(
            schedule_id="sched-1",
            question="What changes in quantum networking?",
            schedule_type="interval",
            interval_minutes=30,
            cron_expression=None,
            timezone="UTC",
            enabled=True,
            created_at=utc(),
            next_run_at=utc(),
            last_status="never_run",
            notification_status="not_configured",
        )
    init_database()  # second call must not drop or recreate anything
    init_database()
    with session_scope() as session:
        assert ScheduleRepository(session).count() == 1


def test_engine_creates_missing_parent_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = tmp_path / "deep" / "nesting" / "researchflow.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{nested}")
    reset_engine_for_tests()
    try:
        get_engine()
        assert nested.is_file()
    finally:
        reset_engine_for_tests()


def test_engine_rebuilt_after_reset_keeps_data(temporary_database: Path) -> None:
    init_database()
    with session_scope() as session:
        ScheduleRepository(session).create(
            schedule_id="sched-survive",
            question="Does this row survive an engine rebuild?",
            schedule_type="interval",
            interval_minutes=45,
            cron_expression=None,
            timezone="UTC",
            enabled=True,
            created_at=utc(),
            next_run_at=None,
            last_status="never_run",
            notification_status="not_configured",
        )
    reset_engine_for_tests()  # simulates a process restart
    with session_scope() as session:
        row = ScheduleRepository(session).get("sched-survive")
        assert row is not None
        assert row.question.startswith("Does this row")


# ------------------------------------------------------------ datetime helpers


def test_to_db_strips_utc_timezone() -> None:
    aware = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
    stored = to_db(aware)
    assert stored is not None and stored.tzinfo is None
    assert stored == datetime(2026, 9, 6, 12, 0, 0)  # noqa: DTZ001 — naive is the contract


def test_to_db_converts_other_timezones_to_utc() -> None:
    jakarta = dt_timezone(timedelta(hours=7))
    aware = datetime(2026, 9, 6, 19, 30, 0, tzinfo=jakarta)  # 12:30 UTC
    stored = to_db(aware)
    assert stored == datetime(2026, 9, 6, 12, 30, 0)  # noqa: DTZ001 — naive is the contract


def test_from_db_restores_utc_timezone() -> None:
    # Naive input is what SQLite actually stores — from_db re-attaches UTC.
    restored = from_db(datetime(2026, 9, 6, 12, 0, 0))  # noqa: DTZ001
    assert restored is not None
    assert restored.tzinfo == UTC


def test_datetime_helpers_pass_none_through() -> None:
    assert to_db(None) is None
    assert from_db(None) is None


# ------------------------------------------------------------ session scope


def test_session_scope_commits(temporary_database: Path) -> None:
    with session_scope() as session:
        ScheduleRepository(session).create(
            schedule_id="sched-commit",
            question="Committed?",
            schedule_type="interval",
            interval_minutes=30,
            cron_expression=None,
            timezone="UTC",
            enabled=True,
            created_at=utc(),
            next_run_at=None,
            last_status="never_run",
            notification_status="not_configured",
        )
    with session_scope() as session:
        assert ScheduleRepository(session).get("sched-commit") is not None


def test_session_scope_rolls_back_on_error(temporary_database: Path) -> None:
    payload = {
        "schedule_id": "sched-rb",
        "question": "Rolled back?",
        "schedule_type": "interval",
        "interval_minutes": 30,
        "cron_expression": None,
        "timezone": "UTC",
        "enabled": True,
        "created_at": utc(),
        "next_run_at": None,
        "last_status": "never_run",
        "notification_status": "not_configured",
    }
    with session_scope() as session:
        ScheduleRepository(session).create(**payload)
    # The duplicate insert fails at commit and is rolled back; the raw
    # IntegrityError is chained as __cause__ for server-side logs only.
    with pytest.raises(PersistenceError) as raised, session_scope() as session:
        ScheduleRepository(session).create(**payload)  # duplicate primary key
    assert isinstance(raised.value.__cause__, IntegrityError)
    with session_scope() as session:
        assert ScheduleRepository(session).count() == 1  # rollback left 1 row


def test_session_scope_maps_failures_to_persistence_error(
    temporary_database: Path,
) -> None:
    payload = {
        "schedule_id": "sched-pe",
        "question": "Mapped?",
        "schedule_type": "interval",
        "interval_minutes": 30,
        "cron_expression": None,
        "timezone": "UTC",
        "enabled": True,
        "created_at": utc(),
        "next_run_at": None,
        "last_status": "never_run",
        "notification_status": "not_configured",
    }
    with session_scope() as session:
        ScheduleRepository(session).create(**payload)
    # Committing a duplicate primary key through session_scope maps the
    # SQLAlchemy failure to a client-safe PersistenceError.
    with pytest.raises(PersistenceError), session_scope() as session:
        ScheduleRepository(session).create(**payload)
    with session_scope() as session:
        assert ScheduleRepository(session).count() == 1


# ------------------------------------------------------------ repositories


def make_schedule(schedule_id: str, question: str = "Q?", created_minute: int = 0):
    return {
        "schedule_id": schedule_id,
        "question": question,
        "schedule_type": "interval",
        "interval_minutes": 30,
        "cron_expression": None,
        "timezone": "Asia/Jakarta",
        "enabled": True,
        "created_at": datetime(2026, 9, 6, 10, created_minute, tzinfo=UTC),
        "next_run_at": datetime(2026, 9, 6, 11, created_minute, tzinfo=UTC),
        "last_status": "never_run",
        "notification_status": "not_configured",
    }


def test_schedule_repository_round_trip(temporary_database: Path) -> None:
    created = utc()
    with session_scope() as session:
        ScheduleRepository(session).create(
            schedule_id="sched-rt",
            question="Round trip?",
            schedule_type="cron",
            interval_minutes=None,
            cron_expression="0 8 * * *",
            timezone="Asia/Jakarta",
            enabled=True,
            created_at=created,
            next_run_at=created + timedelta(hours=1),
            last_status="never_run",
            notification_status="not_configured",
        )
    with session_scope() as session:
        row = ScheduleRepository(session).get("sched-rt")
        assert row is not None
        assert row.question == "Round trip?"
        assert row.schedule_type == "cron"
        assert row.interval_minutes is None
        assert row.cron_expression == "0 8 * * *"
        assert row.timezone == "Asia/Jakarta"
        assert row.enabled is True
        assert row.created_at == to_db(created)  # stored naive UTC
        assert row.last_run_at is None
        assert row.next_run_at == to_db(created + timedelta(hours=1))
        assert row.last_status == "never_run"
        assert row.last_error is None
        assert row.last_report_id is None
        assert row.notification_status == "not_configured"


def test_schedule_repository_get_unknown_is_none(temporary_database: Path) -> None:
    with session_scope() as session:
        assert ScheduleRepository(session).get("ghost") is None


def test_schedule_repository_list_all_oldest_first(temporary_database: Path) -> None:
    with session_scope() as session:
        repo = ScheduleRepository(session)
        repo.create(**make_schedule("sched-b", created_minute=30))
        repo.create(**make_schedule("sched-a", created_minute=0))
        repo.create(**make_schedule("sched-c", created_minute=59))
        ids = [row.id for row in repo.list_all()]
        assert ids == ["sched-a", "sched-b", "sched-c"]
        assert repo.count() == 3


def test_schedule_repository_updates(temporary_database: Path) -> None:
    with session_scope() as session:
        ScheduleRepository(session).create(**make_schedule("sched-upd"))
    moment = utc()
    with session_scope() as session:
        repo = ScheduleRepository(session)
        assert (
            repo.set_enabled("sched-upd", enabled=False, next_run_at=None, updated_at=moment)
            == 1
        )
    with session_scope() as session:
        repo = ScheduleRepository(session)
        repo.mark_started(
            "sched-upd", last_run_at=moment, last_status="running", updated_at=moment
        )
        repo.record_outcome(
            "sched-upd",
            last_status="failed",
            last_error="Search failed.",
            last_report_id=None,
            next_run_at=None,
            updated_at=moment,
        )
        repo.set_notification_status("sched-upd", "sent", updated_at=moment)
    with session_scope() as session:
        row = ScheduleRepository(session).get("sched-upd")
        assert row is not None
        assert row.enabled is False
        assert row.last_run_at == to_db(moment)
        assert row.last_status == "failed"
        assert row.last_error == "Search failed."
        assert row.notification_status == "sent"
        assert row.next_run_at is None
    with session_scope() as session:
        repo = ScheduleRepository(session)
        assert (
            repo.set_enabled("ghost", enabled=True, next_run_at=None, updated_at=moment)
            == 0
        )


def test_schedule_repository_delete(temporary_database: Path) -> None:
    with session_scope() as session:
        ScheduleRepository(session).create(**make_schedule("sched-del"))
    with session_scope() as session:
        ScheduleRepository(session).delete("sched-del")
    with session_scope() as session:
        assert ScheduleRepository(session).get("sched-del") is None
        assert ScheduleRepository(session).count() == 0


def make_execution(execution_id: str, schedule_id: str, minute: int):
    return {
        "execution_id": execution_id,
        "schedule_id": schedule_id,
        "trigger_type": "scheduled",
        "started_at": datetime(2026, 9, 6, 12, minute, tzinfo=UTC),
        "notification_status": "not_configured",
    }


def test_execution_repository_lifecycle(temporary_database: Path) -> None:
    with session_scope() as session:
        ScheduleRepository(session).create(**make_schedule("sched-ex"))
        ExecutionRepository(session).create(**make_execution("exec-1", "sched-ex", 0))
    with session_scope() as session:
        row = ExecutionRepository(session).get("exec-1")
        assert row is not None
        assert row.status == "running"  # created as running
        assert row.completed_at is None
        assert row.trigger_type == "scheduled"
    done = utc()
    with session_scope() as session:
        repo = ExecutionRepository(session)
        assert (
            repo.mark_completed(
                "exec-1",
                status="success",
                completed_at=done,
                error=None,
                report_id="rep-001",
                source_count=4,
            )
            == 1
        )
        repo.set_notification_status("exec-1", "sent")
    with session_scope() as session:
        row = ExecutionRepository(session).get("exec-1")
        assert row is not None
        assert row.status == "success"
        assert row.completed_at == to_db(done)
        assert row.report_id == "rep-001"
        assert row.source_count == 4
        assert row.notification_status == "sent"


def test_execution_repository_list_newest_first_with_limit(
    temporary_database: Path,
) -> None:
    with session_scope() as session:
        schedules = ScheduleRepository(session)
        schedules.create(**make_schedule("sched-h"))
        executions = ExecutionRepository(session)
        for index in range(5):
            executions.create(**make_execution(f"exec-{index}", "sched-h", index))
    with session_scope() as session:
        repo = ExecutionRepository(session)
        ids = [row.id for row in repo.list_for_schedule("sched-h", limit=3)]
        assert ids == ["exec-4", "exec-3", "exec-2"]  # newest first, limited
        assert repo.count_for_schedule("sched-h") == 5
        assert repo.count_for_schedule("ghost") == 0


def test_report_repository_round_trip(temporary_database: Path) -> None:
    created = utc()
    with session_scope() as session:
        ReportRepository(session).create(
            report_id="rep-rt",
            schedule_id=None,
            execution_id=None,
            query="Report metadata round trip?",
            markdown_filename="researchflow_rep-rt.md",
            pdf_filename="researchflow_rep-rt.pdf",
            created_at=created,
            synthesis_status="success",
        )
    with session_scope() as session:
        row = ReportRepository(session).get("rep-rt")
        assert row is not None
        assert row.query == "Report metadata round trip?"
        assert row.markdown_filename == "researchflow_rep-rt.md"
        assert row.pdf_filename == "researchflow_rep-rt.pdf"
        assert row.created_at == to_db(created)
        assert row.synthesis_status == "success"
        assert row.schedule_id is None
        assert row.execution_id is None


def test_report_repository_create_is_idempotent(temporary_database: Path) -> None:
    payload = {
        "report_id": "rep-idem",
        "schedule_id": None,
        "execution_id": None,
        "query": "Idempotent?",
        "markdown_filename": "researchflow_rep-idem.md",
        "pdf_filename": "researchflow_rep-idem.pdf",
        "created_at": utc(),
        "synthesis_status": "success",
    }
    with session_scope() as session:
        ReportRepository(session).create(**payload)
    with session_scope() as session:
        ReportRepository(session).create(**payload)  # second insert is skipped
    with session_scope() as session:
        assert ReportRepository(session).count() == 1


def test_report_repository_list_recent_and_count(temporary_database: Path) -> None:
    with session_scope() as session:
        repo = ReportRepository(session)
        for index in range(4):
            repo.create(
                report_id=f"rep-{index}",
                schedule_id=None,
                execution_id=None,
                query=f"Q{index}",
                markdown_filename=f"researchflow_rep-{index}.md",
                pdf_filename=f"researchflow_rep-{index}.pdf",
                created_at=datetime(2026, 9, 6, 13, index, tzinfo=UTC),
                synthesis_status="success",
            )
    with session_scope() as session:
        repo = ReportRepository(session)
        ids = [row.id for row in repo.list_recent(limit=2)]
        assert ids == ["rep-3", "rep-2"]  # newest first
        assert repo.count() == 4


# ------------------------------------------------------------ foreign keys


def test_deleting_schedule_preserves_history_via_set_null(
    temporary_database: Path,
) -> None:
    moment = utc()
    with session_scope() as session:
        ScheduleRepository(session).create(**make_schedule("sched-fk"))
        ExecutionRepository(session).create(**make_execution("exec-fk", "sched-fk", 0))
        ReportRepository(session).create(
            report_id="rep-fk",
            schedule_id="sched-fk",
            execution_id="exec-fk",
            query="History preserved?",
            markdown_filename="researchflow_rep-fk.md",
            pdf_filename="researchflow_rep-fk.pdf",
            created_at=moment,
            synthesis_status="success",
        )
    with session_scope() as session:
        ScheduleRepository(session).delete("sched-fk")
    with session_scope() as session:
        executions = ExecutionRepository(session)
        row = executions.get("exec-fk")
        assert row is not None  # the execution row SURVIVED the delete
        assert row.schedule_id is None  # ...with the FK nulled
        reports = ReportRepository(session)
        meta = reports.get("rep-fk")
        assert meta is not None
        assert meta.schedule_id is None
        assert meta.execution_id == "exec-fk"  # execution link intact
        assert ScheduleRepository(session).get("sched-fk") is None


def test_foreign_key_violation_is_rejected(temporary_database: Path) -> None:
    with pytest.raises(PersistenceError), session_scope() as session:
        # schedule_id references a schedule that does not exist
        ExecutionRepository(session).create(**make_execution("exec-bad", "ghost", 0))
