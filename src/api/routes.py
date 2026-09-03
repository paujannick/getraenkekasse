"""REST-API v1 – schlank, tokenbasiert, mit strengen Rate-Limits.

Endpunkte:
* GET  /api/v1/health                  – Public
* GET  /api/v1/drinks                  – read
* GET  /api/v1/users/by-uid/<uid>      – read
* GET  /api/v1/users/<uid>/balance     – read
* POST /api/v1/topup                   – write   {uid, amount_cents}
* POST /api/v1/purchase                – write   {uid, drink_id, quantity}

Antworten sind immer JSON. Fehlercodes folgen HTTP-Status.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from flask import Blueprint, jsonify, request

from .. import audit, models
from ..discounts import effective_price
from . import tokens as api_tokens

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def require_scope(min_scope: str) -> Callable:
    order = {"read": 0, "write": 1, "admin": 2}

    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else ""
            rec = api_tokens.verify_token(token)
            if not rec:
                return jsonify({"error": "unauthorized"}), 401
            if order.get(rec.scopes, -1) < order[min_scope]:
                return jsonify({"error": "forbidden", "required_scope": min_scope}), 403
            request.api_token = rec  # type: ignore[attr-defined]
            return fn(*args, **kwargs)

        return wrapper

    return deco


@api_bp.get("/health")
def health():
    return jsonify({"status": "ok", "service": "getraenkekasse", "version": "2.0"})


@api_bp.get("/drinks")
@require_scope("read")
def list_drinks():
    drinks = models.get_drinks()
    return jsonify(
        [
            {
                "id": d.id,
                "name": d.name,
                "price_cents": d.price,
                "effective_price_cents": effective_price(d.price, d.id),
                "stock": d.stock,
                "min_stock": d.min_stock,
                "page": d.page,
                "image": d.image,
            }
            for d in drinks
        ]
    )


@api_bp.get("/users/by-uid/<uid>")
@require_scope("read")
def user_by_uid(uid: str):
    user = models.get_user_by_uid(uid)
    if not user:
        return jsonify({"error": "not_found"}), 404
    return jsonify(
        {"id": user.id, "name": user.name, "balance_cents": user.balance,
         "is_event": bool(user.is_event), "active": bool(user.active)}
    )


@api_bp.get("/users/<uid>/balance")
@require_scope("read")
def user_balance(uid: str):
    user = models.get_user_by_uid(uid)
    if not user:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"uid": uid, "balance_cents": user.balance})


@api_bp.post("/topup")
@require_scope("write")
def topup():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or "").strip()
    amount = int(data.get("amount_cents") or 0)
    if not uid or amount <= 0:
        return jsonify({"error": "bad_request"}), 400
    user = models.get_user_by_uid(uid)
    if not user:
        return jsonify({"error": "not_found"}), 404
    ok = models.update_balance(user.id, amount)
    if not ok:
        return jsonify({"error": "topup_failed"}), 400
    models.add_topup(user.id, amount)
    audit.log(
        "api.topup",
        actor=f"api:{request.api_token.name}",  # type: ignore[attr-defined]
        ip=_client_ip(),
        target=f"user:{user.id}",
        details=f"amount_cents={amount}",
    )
    fresh = models.get_user(user.id)
    return jsonify({"ok": True, "balance_cents": fresh.balance if fresh else user.balance + amount})


@api_bp.post("/purchase")
@require_scope("write")
def purchase():
    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid") or "").strip()
    drink_id = int(data.get("drink_id") or 0)
    qty = max(1, int(data.get("quantity") or 1))
    if not uid or drink_id <= 0:
        return jsonify({"error": "bad_request"}), 400

    user = models.get_user_by_uid(uid)
    drink = models.get_drink_by_id(drink_id)
    if not user or not drink:
        return jsonify({"error": "not_found"}), 404

    if drink.stock < qty:
        return jsonify({"error": "out_of_stock", "stock": drink.stock}), 409

    unit_price = effective_price(drink.price, drink.id)
    total = unit_price * qty
    if user.is_event == 0 and not models.update_balance(user.id, -total):
        return jsonify({"error": "insufficient_funds"}), 402
    if not models.update_drink_stock(drink.id, -qty):
        # Bestandsupdate fehlgeschlagen – Guthaben zurückrollen.
        if user.is_event == 0:
            models.update_balance(user.id, total)
        return jsonify({"error": "stock_update_failed"}), 500
    models.add_transaction(user.id, drink.id, qty)
    audit.log(
        "api.purchase",
        actor=f"api:{request.api_token.name}",  # type: ignore[attr-defined]
        ip=_client_ip(),
        target=f"user:{user.id}",
        details=f"drink={drink.id} qty={qty} total_cents={total}",
    )
    return jsonify(
        {
            "ok": True,
            "unit_price_cents": unit_price,
            "total_cents": total,
            "balance_cents": models.get_user(user.id).balance if user.is_event == 0 else None,
        }
    )


def register_api(app) -> None:
    app.register_blueprint(api_bp)
