"""API tests for POST /api/research/report and the download endpoints.

Synthesis is fully mocked via dependency overrides and reports are written
to a tmp directory — no GLM, no ChromaDB, no real web requests, no cost.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.research import get_report_service, get_synthesis_service
from app.core import config
from app.main import app
from app.services.report_service import ReportService
from app.services.synthesis_service import (
    CitationRecord,
    SynthesisOutcome,
    SynthesisService,
    SynthesisServiceError,
)

client = TestClient(app)

VALID_REQUEST = {"query": "What are the latest RAG techniques?", "top_k": 5}


class FakeSynthesisService(SynthesisService):
    """Scripted service — returns a preset outcome or raises; counts calls."""

    def __init__(
        self,
        outcome: SynthesisOutcome | None = None,
        error: SynthesisServiceError | None = None,
    ) -> None:
        self._outcome = outcome
        self._error = error
        self.calls = 0

    async def synthesize(self, query: str, top_k: int) -> SynthesisOutcome:
        self.calls += 1
        if self._error:
            raise self._error
        assert self._outcome is not None
        return self._outcome


@pytest.fixture
def use_fake_synthesis():
    def _install(fake: FakeSynthesisService) -> FakeSynthesisService:
        app.dependency_overrides[get_synthesis_service] = lambda: fake
        return fake

    yield _install
    app.dependency_overrides.pop(get_synthesis_service, None)


@pytest.fixture
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Route report generation AND downloads to a tmp directory."""
    directory = tmp_path / "reports"
    monkeypatch.setattr(config, "REPORTS_DIR", directory)
    service = ReportService(reports_dir=directory)
    app.dependency_overrides[get_report_service] = lambda: service
    yield directory
    app.dependency_overrides.pop(get_report_service, None)


def _citation(evidence_id: str, position: int) -> CitationRecord:
    return CitationRecord(
        evidence_id=evidence_id,
        source_title=f"Research Source {position}",
        source_url=f"https://example.com/source-{position}",
        source_domain="example.com",
        chunk_index=position,
    )


def _grounded_outcome(**overrides) -> SynthesisOutcome:
    defaults: dict = {
        "status": "success",
        "query": VALID_REQUEST["query"],
        "answer": "Hybrid retrieval dominates modern RAG [E1][E2].",
        "citations": [_citation("E1", 1), _citation("E2", 2)],
        "evidence_count": 2,
    }
    defaults.update(overrides)
    return SynthesisOutcome(**defaults)


# ------------------------------------------------------------- POST /report


def test_report_success_returns_metadata_and_urls(
    use_fake_synthesis, reports_dir: Path
) -> None:
    fake = use_fake_synthesis(FakeSynthesisService(outcome=_grounded_outcome()))

    response = client.post("/api/research/report", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["synthesis_status"] == "success"
    assert body["query"] == VALID_REQUEST["query"]
    assert body["markdown_filename"].startswith("researchflow_")
    assert body["markdown_filename"].endswith(".md")
    assert body["pdf_filename"].endswith(".pdf")
    assert body["markdown_download_url"].endswith(f"/{body['report_id']}/markdown")
    assert body["pdf_download_url"].endswith(f"/{body['report_id']}/pdf")
    # Synthesis runs exactly once — no extra GLM call for the report.
    assert fake.calls == 1
    # Both files really exist under the reports directory.
    assert (reports_dir / body["markdown_filename"]).is_file()
    assert (reports_dir / body["pdf_filename"]).is_file()


def test_report_response_leaks_no_paths_or_secrets(
    use_fake_synthesis, reports_dir: Path
) -> None:
    use_fake_synthesis(FakeSynthesisService(outcome=_grounded_outcome()))

    body = client.post("/api/research/report", json=VALID_REQUEST).json()

    raw = str(body)
    assert str(reports_dir) not in raw
    assert "/home/" not in raw
    assert "GLM_API_KEY" not in raw
    assert ".env" not in raw


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "ab"},
        {"query": ""},
        {"query": "x" * 2001},
        {"query": "valid query here", "top_k": 0},
        {"query": "valid query here", "top_k": 21},
        {"query": "valid query here", "top_k": -3},
        {},
    ],
)
def test_report_invalid_request_is_422(reports_dir: Path, payload: dict) -> None:
    assert (
        client.post("/api/research/report", json=payload).status_code == 422
    )


def test_report_insufficient_evidence_still_generates(
    use_fake_synthesis, reports_dir: Path
) -> None:
    use_fake_synthesis(
        FakeSynthesisService(
            outcome=SynthesisOutcome(
                status="insufficient_evidence",
                query=VALID_REQUEST["query"],
                answer="Not enough indexed evidence.",
                citations=[],
                evidence_count=0,
            )
        )
    )

    response = client.post("/api/research/report", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["synthesis_status"] == "insufficient_evidence"
    markdown = (reports_dir / body["markdown_filename"]).read_text()
    assert "Insufficient Evidence" in markdown


@pytest.mark.parametrize(
    ("http_status", "message"),
    [
        (502, "The AI could not produce a valid grounded answer."),
        (504, "GLM API request timed out."),
        (503, "GLM API is not configured."),
    ],
)
def test_report_synthesis_failure_propagates_status(
    use_fake_synthesis, reports_dir: Path, http_status: int, message: str
) -> None:
    use_fake_synthesis(FakeSynthesisService(error=SynthesisServiceError(message, http_status)))

    response = client.post("/api/research/report", json=VALID_REQUEST)

    assert response.status_code == http_status
    assert response.json()["detail"] == message
    # Nothing was written when synthesis failed.
    assert not any(reports_dir.glob("researchflow_*"))


def test_report_generation_error_is_500(
    use_fake_synthesis, reports_dir: Path
) -> None:
    use_fake_synthesis(FakeSynthesisService(outcome=_grounded_outcome()))

    class ExplodingService(ReportService):
        def generate(self, outcome):
            raise RuntimeError("boom")

    app.dependency_overrides[get_report_service] = lambda: ExplodingService(
        reports_dir=reports_dir
    )
    try:
        response = client.post("/api/research/report", json=VALID_REQUEST)
    finally:
        # Restore the fixture's normal service for any later assertions.
        app.dependency_overrides[get_report_service] = lambda: ReportService(
            reports_dir=reports_dir
        )

    assert response.status_code == 500
    assert "boom" not in response.json()["detail"]
    assert "Traceback" not in response.json()["detail"]


# ------------------------------------------------------------------ downloads


def _generate_report(reports_dir: Path) -> dict:
    fake = FakeSynthesisService(outcome=_grounded_outcome())
    app.dependency_overrides[get_synthesis_service] = lambda: fake
    try:
        response = client.post("/api/research/report", json=VALID_REQUEST)
    finally:
        app.dependency_overrides.pop(get_synthesis_service, None)
    assert response.status_code == 200
    return response.json()


def test_download_markdown_returns_file(reports_dir: Path) -> None:
    body = _generate_report(reports_dir)

    response = client.get(f"/api/research/reports/{body['report_id']}/markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "# Research Report" in response.text
    assert "https://example.com/source-1" in response.text


def test_download_pdf_returns_file(reports_dir: Path) -> None:
    body = _generate_report(reports_dir)

    response = client.get(f"/api/research/reports/{body['report_id']}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


def test_download_missing_report_is_404(reports_dir: Path) -> None:
    for url in (
        "/api/research/reports/does-not-exist_12345678/markdown",
        "/api/research/reports/does-not-exist_12345678/pdf",
    ):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json()["detail"] == "Report not found."


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../etc/passwd_12345678",
        "..%2F..%2Fetc%2Fpasswd_12345678",
        "slug/../../../etc/passwd_12345678",
        "slug\\..\\..\\etc_12345678",
        "nul%00byte_12345678",
        "slug_12345678.pdf",
        ".gitkeep",
        "researchflow_report_12345678.md",
        "SLUG_12345678",
        "slug_1234567",
        "slug_x12345678",
        "",
    ],
)
def test_download_rejects_malformed_ids(reports_dir: Path, bad_id: str) -> None:
    for suffix in ("markdown", "pdf"):
        url = f"/api/research/reports/{bad_id}/{suffix}"
        assert client.get(url).status_code == 404, url


def test_download_never_serves_files_outside_reports_dir(reports_dir: Path) -> None:
    # Even an id that parses must resolve inside the reports dir; probing
    # .gitkeep / arbitrary names is impossible by construction (404).
    assert (
        client.get("/api/research/reports/reports/.gitkeep_12345678/markdown").status_code
        == 404
    )
    assert (
        client.get("/api/research/reports/../../../../etc/passwd/markdown").status_code
        in (404, 422)
    )


# ---------------------------------------------------------- existing contracts


def test_all_six_prior_endpoints_still_registered(reports_dir: Path) -> None:
    """Steps 1–5 endpoints must remain untouched by Step 6."""
    assert client.get("/health").status_code == 200
    for path, payload in (
        ("/api/research/plan", {"question": "x"}),
        ("/api/research/search", {"question": "x"}),
        ("/api/research/index", {"sources": []}),
        ("/api/research/retrieve", {"query": "x"}),
        ("/api/research/synthesize", {"query": "x"}),
    ):
        assert client.post(path, json=payload).status_code == 422, path
