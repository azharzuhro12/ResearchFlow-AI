"""Tests for the FastAPI application endpoints."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_returns_running_message() -> None:
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "ResearchFlow AI API is running"
    assert data["version"] == "0.1.0"


def test_health_returns_healthy() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
