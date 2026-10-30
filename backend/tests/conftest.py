import shutil
import time

import pytest

from app import auth, db
from app.generator import seed_database

PW = "voterengine"
H = {"X-Requested-With": "VoterEngine"}   # CSRF header the web client sends


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    """Seed once per session; each test gets a fresh copy."""
    path = tmp_path_factory.mktemp("seed") / "seed.db"
    with db.session(path) as c:
        seed_database(c, seed=1)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return path


@pytest.fixture()
def app_db(tmp_path, monkeypatch, seeded_db):
    path = tmp_path / "test.db"
    shutil.copy(seeded_db, path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.set_workspace("main")          # requests set this per call; direct db use in tests means the real workspace
    auth._FAILS.clear()
    return path


class Client:
    """Flask test client that remembers its session cookie and sends the CSRF header."""

    def __init__(self, flask_client):
        self.c = flask_client

    def login(self, username, password=PW):
        r = self.c.post("/api/auth/login", json={"username": username, "password": password}, headers=H)
        assert r.status_code == 200, r.get_json()
        return self

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers={**H, **kw.pop("headers", {})}, **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers={**H, **kw.pop("headers", {})}, **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers={**H, **kw.pop("headers", {})}, **kw)


@pytest.fixture()
def make_client(app_db):
    from app.main import app
    app.config["TESTING"] = True

    def make(username=None):
        cl = Client(app.test_client())
        return cl.login(username) if username else cl
    return make


@pytest.fixture()
def admin(make_client):
    return make_client("admin")


@pytest.fixture()
def client(admin):
    """Back-compat name used by the pipeline tests: an admin session."""
    return admin


def wait_job(cl, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = cl.get(f"/api/scoring/jobs/{job_id}").get_json()
        if j["status"] in ("DONE", "FAILED"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")
