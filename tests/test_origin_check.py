"""Same-origin check on state-changing requests (app/origin_check.py)."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.db import get_session
from app.main import app
from app.origin_check import SameOriginMiddleware

HOST = "narrator.rch-anaesthesia.org"
OWN = f"https://{HOST}"
SIBLING = "https://evil.rch-anaesthesia.org"
FOREIGN = "https://example.com"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    # No context manager, so the lifespan (which creates the on-disk DB) never runs.
    c = TestClient(app, base_url=f"http://{HOST}", follow_redirects=False)
    yield c
    app.dependency_overrides.clear()


def _post_case(client, **headers):
    return client.post("/cases", data={"weight_kg": "20"},
                       headers={"x-forwarded-proto": "https", **headers})


def test_same_origin_fetch_site_passes(client):
    r = _post_case(client, **{"sec-fetch-site": "same-origin", "origin": OWN})
    assert r.status_code == 303


def test_fetch_site_none_passes(client):
    assert _post_case(client, **{"sec-fetch-site": "none"}).status_code == 303


def test_origin_null_with_same_origin_fetch_site_passes(client):
    r = _post_case(client, **{"sec-fetch-site": "same-origin", "origin": "null"})
    assert r.status_code == 303


def test_origin_null_alone_passes(client):
    assert _post_case(client, origin="null").status_code == 303


def test_sibling_subdomain_same_site_refused(client):
    r = _post_case(client, **{"sec-fetch-site": "same-site", "origin": SIBLING})
    assert r.status_code == 403


def test_cross_site_refused(client):
    r = _post_case(client, **{"sec-fetch-site": "cross-site", "origin": FOREIGN})
    assert r.status_code == 403


def test_no_headers_passes(client):
    assert _post_case(client).status_code == 303


def test_origin_only_matching_passes(client):
    assert _post_case(client, origin=OWN).status_code == 303


def test_origin_only_sibling_refused(client):
    assert _post_case(client, origin=SIBLING).status_code == 403


def test_origin_only_wrong_scheme_refused(client):
    # Behind Caddy the app is https; an http Origin is a different origin.
    assert _post_case(client, origin=f"http://{HOST}").status_code == 403


@pytest.mark.parametrize("method", ["put", "delete"])
def test_other_write_methods_checked(client, method):
    r = client.request(method.upper(), "/case/1/events/1",
                       headers={"sec-fetch-site": "same-site", "origin": SIBLING})
    assert r.status_code == 403


def test_reads_are_not_checked(client):
    r = client.get("/", headers={"sec-fetch-site": "cross-site", "origin": FOREIGN})
    assert r.status_code == 200


def test_healthz_unaffected(client):
    r = client.get("/healthz", headers={"sec-fetch-site": "cross-site", "origin": FOREIGN})
    assert r.status_code == 200 and r.text == "ok"


def test_static_unaffected(client):
    r = client.get("/static/app.css", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 200


def _bare_app(public_url):
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return TestClient(SameOriginMiddleware(ok, public_url=public_url),
                      base_url="http://internal:8000")


def test_configured_public_url_is_the_own_origin():
    c = _bare_app(OWN)
    assert c.post("/x", headers={"origin": OWN}).status_code == 200
    assert c.post("/x", headers={"origin": "http://internal:8000"}).status_code == 403


def test_default_port_is_normalised():
    c = _bare_app(OWN)
    assert c.post("/x", headers={"origin": f"{OWN}:443"}).status_code == 200
