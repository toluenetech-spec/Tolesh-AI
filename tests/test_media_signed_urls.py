"""Signed artifact URLs: the fix for previews under a token-guarded API.

An ``<img src>`` cannot send an ``Authorization`` header, so with a token set
every chart tile and file preview returned 401. The fix is a per-artifact
signature rather than putting the API token in the URL — these tests hold both
halves of that: the signature works without a header, and it is narrow.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tolesh.server import create_app
from tolesh.server.app import AppState
from tolesh.server import media_urls

pytestmark = pytest.mark.integration

TOKEN = "test-token-for-signed-urls"


@pytest.fixture
def client(tmp_path):
    state = AppState(in_memory=True, memory_debounce=0.01)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False,
                     api_token=TOKEN)
    with TestClient(app) as test_client:
        yield test_client, state
    state.close()


def _artifact(client):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    state = client.app.state.tolesh
    return state.artifacts.save_bytes("dot.png", png, kind="image", mime="image/png")


def test_artifact_url_is_signed_and_works_without_a_header(client):
    api, _state = client
    artifact = _artifact(api)
    listed = api.get("/api/artifacts",
                     headers={"Authorization": f"Bearer {TOKEN}"}).json()["artifacts"]
    url = [item for item in listed if item["id"] == artifact["id"]][0]["download_url"]
    assert "sig=" in url and "exp=" in url
    assert TOKEN not in url, "the API token must never appear in a URL"

    # Exactly what an <img> does: no headers at all.
    response = api.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")


def test_the_bare_download_path_is_still_protected(client):
    api, _state = client
    artifact = _artifact(api)
    assert api.get(f"/api/artifacts/{artifact['id']}/download").status_code == 401


def test_a_signature_for_another_artifact_does_not_work(client):
    api, state = client
    artifact = _artifact(api)
    other = state.artifacts.save_bytes("other.png", b"\x89PNG\r\n\x1a\n",
                                       kind="image", mime="image/png")
    params = media_urls.sign(state.media_secret, other["id"])
    forged = (f"/api/artifacts/{artifact['id']}/download"
              f"?exp={params['exp']}&sig={params['sig']}")
    assert api.get(forged).status_code == 401


def test_expired_signatures_are_rejected(client):
    api, state = client
    artifact = _artifact(api)
    params = media_urls.sign(state.media_secret, artifact["id"], ttl=-10,
                             now=time.time())
    stale = (f"/api/artifacts/{artifact['id']}/download"
             f"?exp={params['exp']}&sig={params['sig']}")
    assert api.get(stale).status_code == 401


def test_signing_secret_is_per_process(client):
    _api, state = client
    assert len(state.media_secret) == 32
    signature = media_urls.sign(state.media_secret, "abc")
    assert not media_urls.verify(b"0" * 32, "abc", exp=signature["exp"],
                                 sig=signature["sig"])
