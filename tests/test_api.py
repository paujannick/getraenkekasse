from __future__ import annotations

import json

import pytest

from src import database
from src.api import tokens as api_tokens


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setenv("GK_BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key-for-tests-only")
    from src.web import admin_server

    app = admin_server.create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public(app):
    resp = app.test_client().get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json["status"] == "ok"


def test_missing_token_is_rejected(app):
    resp = app.test_client().get("/api/v1/drinks")
    assert resp.status_code == 401


def test_read_token_can_list_drinks(app):
    token, _ = api_tokens.issue_token("test", "read")
    resp = app.test_client().get("/api/v1/drinks", headers=_auth(token))
    assert resp.status_code == 200
    assert isinstance(resp.json, list)


def test_write_scope_required_for_topup(app):
    read_token, _ = api_tokens.issue_token("t1", "read")
    resp = app.test_client().post(
        "/api/v1/topup",
        headers=_auth(read_token),
        data=json.dumps({"uid": "TESTCARD123", "amount_cents": 100}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_topup_updates_balance(app):
    write_token, _ = api_tokens.issue_token("t2", "write")
    client = app.test_client()
    before = client.get("/api/v1/users/by-uid/TESTCARD123", headers=_auth(write_token)).json
    resp = client.post(
        "/api/v1/topup",
        headers=_auth(write_token),
        data=json.dumps({"uid": "TESTCARD123", "amount_cents": 250}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.data
    assert resp.json["balance_cents"] == before["balance_cents"] + 250


def test_purchase_deducts_balance_and_stock(app):
    write_token, _ = api_tokens.issue_token("t3", "write")
    client = app.test_client()
    drinks = client.get("/api/v1/drinks", headers=_auth(write_token)).json
    drink = drinks[0]

    resp = client.post(
        "/api/v1/purchase",
        headers=_auth(write_token),
        data=json.dumps({"uid": "TESTCARD123", "drink_id": drink["id"], "quantity": 1}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json
    assert body["ok"]
    assert body["total_cents"] == drink["effective_price_cents"]


def test_healthz_public(app):
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
