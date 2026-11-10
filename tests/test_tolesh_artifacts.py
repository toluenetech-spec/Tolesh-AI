"""Phase 9 — data analysis, charts, reports and downloadable artifacts.

The CSV is real, pandas computes the statistics, matplotlib draws the PNG.
Nothing here is asserted from a stub.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tolesh.artifacts import (AnalysisError, ArtifactStore, chart_png, report_html,
                              safe_filename, summarise, summarise_text)
from tolesh.server import create_app
from tolesh.server.app import AppState

CSV = b"""name,region,amount,units
alpha,EU,10,1
beta,EU,20,2
gamma,US,30,3
delta,US,,4
"""

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------- store
def test_artifact_names_cannot_escape_their_directory(tmp_path):
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..") == "artifact"
    assert "/" not in safe_filename("a/b/c.png")


def test_store_saves_lists_reads_and_deletes(tmp_path):
    store = ArtifactStore(str(tmp_path / "a.db"), root=tmp_path / "files")
    artifact = store.save_text("notes.md", "# hello")
    assert artifact["name"] == "notes.md"
    assert artifact["kind"] == "document"
    assert artifact["size"] > 0
    assert store.path(artifact["id"]).read_text() == "# hello"

    assert store.list()[0]["id"] == artifact["id"]
    assert store.stats()["count"] == 1

    assert store.delete(artifact["id"]) is True
    assert store.list() == []
    assert not (tmp_path / "files" / artifact["id"]).exists()


def test_store_rejects_oversized_artifacts(tmp_path):
    store = ArtifactStore(str(tmp_path / "a.db"), root=tmp_path / "files")
    with pytest.raises(ValueError):
        store.save_bytes("big.bin", b"x" * (26 * 1024 * 1024))


def test_kind_is_inferred_from_the_file(tmp_path):
    store = ArtifactStore(str(tmp_path / "a.db"), root=tmp_path / "files")
    png = store.save_bytes("chart.png", PNG_MAGIC, mime="image/png")
    assert png["kind"] == "image"
    html = store.save_bytes("report.html", b"<h1>x</h1>", mime="text/html")
    assert html["kind"] == "report"


# ---------------------------------------------------------------- analysis
def test_summarise_reports_shape_types_and_missing_values():
    profile = summarise(CSV, name="sales.csv")
    assert profile["rows"] == 4
    assert [c["name"] for c in profile["columns"]] == ["name", "region", "amount", "units"]
    assert profile["missing_total"] == 1
    amount = next(c for c in profile["columns"] if c["name"] == "amount")
    assert amount["min"] == 10 and amount["max"] == 30
    assert amount["mean"] == 20
    text = summarise_text(profile)
    assert "4 rows" in text and "amount" in text


def test_summarise_refuses_a_non_table():
    with pytest.raises(AnalysisError):
        summarise(b"\x00\x01\x02 not a csv \xff", name="junk.csv")


def test_chart_is_a_real_png_with_the_requested_columns():
    png, meta = chart_png(CSV, chart_type="bar", x="region", y="amount",
                          title="Revenue by region")
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 2000  # a real drawing, not a blank 1x1
    assert meta["x"] == "region" and meta["y"] == "amount"


def test_chart_rejects_unknown_columns_and_types():
    with pytest.raises(AnalysisError) as excinfo:
        chart_png(CSV, chart_type="bar", x="nope", y="amount")
    assert "Columns:" in str(excinfo.value)
    with pytest.raises(AnalysisError):
        chart_png(CSV, chart_type="bar", x="name", y="name")
    with pytest.raises(AnalysisError):
        chart_png(CSV, chart_type="spiral")


def test_histogram_needs_a_numeric_column():
    png, _ = chart_png(CSV, chart_type="hist", x="amount")
    assert png.startswith(PNG_MAGIC)
    with pytest.raises(AnalysisError):
        chart_png(CSV, chart_type="hist", x="name")


def test_report_escapes_user_text():
    page = report_html("Q1 <script>", [{"heading": "Risks", "body": "<b>none</b>"}])
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "&lt;b&gt;none&lt;/b&gt;" in page
    assert "Q1 &lt;script&gt;" in page


# --------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


def _upload(client: TestClient) -> str:
    response = client.post("/api/attachments?filename=sales.csv", content=CSV,
                           headers={"Content-Type": "text/csv"})
    assert response.status_code == 200, response.text
    return response.json()["attachment"]["id"]


def test_analyse_endpoint_returns_real_statistics(client):
    attachment_id = _upload(client)
    payload = client.post("/api/artifacts/analyse",
                          json={"attachment_id": attachment_id}).json()
    assert payload["profile"]["rows"] == 4
    assert "amount" in payload["summary"]


def test_chart_endpoint_creates_a_downloadable_artifact(client):
    attachment_id = _upload(client)
    created = client.post("/api/artifacts/chart", json={
        "attachment_id": attachment_id, "chart_type": "bar",
        "x": "region", "y": "amount", "title": "Revenue",
    })
    assert created.status_code == 200, created.text
    artifact = created.json()["artifact"]
    assert artifact["kind"] == "image"

    download = client.get(artifact["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(PNG_MAGIC)
    assert "sales" in download.headers.get("content-disposition", "") or \
           "bar-region" in download.headers.get("content-disposition", "")

    listed = client.get("/api/artifacts").json()
    assert listed["artifacts"][0]["id"] == artifact["id"]
    assert listed["stats"]["count"] == 1


def test_chart_errors_are_400_with_a_useful_message(client):
    attachment_id = _upload(client)
    response = client.post("/api/artifacts/chart", json={
        "attachment_id": attachment_id, "chart_type": "bar", "x": "missing"})
    assert response.status_code == 400
    assert "Columns:" in response.json()["detail"]


def test_unknown_attachment_and_artifact_are_404(client):
    assert client.post("/api/artifacts/analyse",
                       json={"attachment_id": "nope"}).status_code == 404
    assert client.get("/api/artifacts/nope").status_code == 404
    assert client.get("/api/artifacts/nope/download").status_code == 404
    assert client.delete("/api/artifacts/nope").status_code == 404


def test_artifact_can_be_deleted(client):
    attachment_id = _upload(client)
    artifact = client.post("/api/artifacts/chart", json={
        "attachment_id": attachment_id, "chart_type": "line",
        "x": "name", "y": "units"}).json()["artifact"]
    assert client.delete(f"/api/artifacts/{artifact['id']}").json()["status"] == "ok"
    assert client.get(f"/api/artifacts/{artifact['id']}").status_code == 404
