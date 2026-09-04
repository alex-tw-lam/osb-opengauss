"""GET /healthz: one SELECT 1 over the real admin connection path."""

from conftest import make_env

from osb_opengauss.app import create_app
from osb_opengauss.gaussdb import GaussDBAdmin


def test_healthz_ok_without_authentication(tmp_path):
    env = make_env(tmp_path)
    app = create_app(env.settings, admin=GaussDBAdmin(env.settings, connect=env.fake))
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert "SELECT 1" in env.fake.sql_text()


class BrokenConnect:
    def __call__(self, dbname=None):
        raise RuntimeError("connection refused")


def test_healthz_reports_unreachable_database(tmp_path):
    env = make_env(tmp_path)
    app = create_app(env.settings, admin=GaussDBAdmin(env.settings, connect=BrokenConnect()))
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "unreachable"
    assert "connection refused" in body["error"]
