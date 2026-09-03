"""User-Self-Service.

Der Nutzer öffnet einen signierten Link (aus dem QR-Code auf seiner Karte)
und sieht Guthaben, letzte Käufe und Aufladungen. Der Link ist zeitlich
begrenzt (7 Tage) und kann jederzeit durch Wechseln des SECRET_KEY
invalidiert werden.
"""

from __future__ import annotations

import io
from pathlib import Path

import qrcode
from flask import Blueprint, abort, current_app, render_template, send_file, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .. import models

selfservice_bp = Blueprint(
    "selfservice",
    __name__,
    url_prefix="/me",
    template_folder=str(Path(__file__).resolve().parent.parent / "web" / "templates"),
)

# 7 Tage
TOKEN_MAX_AGE = 7 * 24 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="gkasse-selfservice")


def make_token(uid: str) -> str:
    return _serializer().dumps(uid)


def parse_token(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


@selfservice_bp.get("/<token>")
def show(token: str):
    uid = parse_token(token)
    if not uid:
        abort(404)
    user = models.get_user_by_uid(uid)
    if not user:
        abort(404)
    with __import__("src.database", fromlist=["get_connection"]).get_connection() as conn:
        transactions = conn.execute(
            "SELECT t.timestamp, d.name AS drink, t.quantity, d.price "
            "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
            "WHERE t.user_id=? ORDER BY t.id DESC LIMIT 20",
            (user.id,),
        ).fetchall()
        topups = conn.execute(
            "SELECT timestamp, amount FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 20",
            (user.id,),
        ).fetchall()
    return render_template(
        "selfservice.html",
        user=user,
        transactions=transactions,
        topups=topups,
    )


@selfservice_bp.get("/<token>/qr.png")
def qr(token: str):
    uid = parse_token(token)
    if not uid:
        abort(404)
    from .. import network
    url = network.public_base_url() + url_for("selfservice.show", token=token)
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")
