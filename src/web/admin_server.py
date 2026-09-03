"""Web-Admin + Kiosk (Flask 3, Argon2, CSRF, Rate-Limit, SSE, Multi-Admin).

Startbar über ``python -m src.web.admin_server`` (Waitress in Produktion).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf
from fpdf import FPDF
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import (
    activity,
    admin_auth,
    audit,
    backups,
    database,
    discounts,
    ledger,
    logging_setup,
    migrations,
    models,
    network,
    recurring,
    rfid_bridge,
    scheduler,
    security,
    webhooks,
)
from .. import (
    admins as admins_mod,
)
from .. import (
    kiosk as kiosk_mod,
)
from ..api.routes import register_api
from ..api.selfservice import make_token, selfservice_bp
from ..api.tokens import ensure_schema as ensure_token_schema
from ..api.tokens import issue_token, list_tokens, revoke
from ..telegram_bot import notifier

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_filter_clause(period: str) -> str:
    if period == "day":
        return "date('now', '-1 day')"
    if period == "week":
        return "date('now', '-7 day')"
    return "date('now', '-1 month')"


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def _current_user() -> str | None:
    return session.get("user")


def _current_role() -> str:
    return session.get("role") or "superadmin"  # Datei-Admin ist superadmin


def _audit(action: str, target: str | None = None, details: str | None = None) -> None:
    audit.log(action, actor=_current_user(), ip=_client_ip(), target=target, details=details)


def _act(kind: str, target: str | None = None, amount_cents: int | None = None, note: str | None = None) -> None:
    activity.log(kind, actor=_current_user(), target=target, amount_cents=amount_cents, note=note)


def _hx_trigger(payload: dict) -> tuple[str, str]:
    """Fasst mehrere Client-Events für HTMX in einem Header zusammen."""
    return "HX-Trigger", json.dumps(payload)


# ---------------------------------------------------------------------------
# App-Factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    load_dotenv(override=False)
    logging_setup.configure()

    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static",
    )
    app.secret_key = security.get_or_create_secret_key()
    security.apply_session_hardening(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Datenbank + Migrations (mit Auto-Backup) + Zusatz-Tabellen.
    try:
        applied = migrations.upgrade()
        if applied:
            _LOG.info("applied migrations: %s", applied)
    except Exception as e:
        _LOG.exception("migration failed: %s", e)
        raise
    audit.ensure_schema()
    discounts.ensure_schema()
    ensure_token_schema()

    # RFID-Hardware-Poller (macht auf Windows nichts, das ist ok).
    rfid_bridge.start()
    scheduler.start()

    csrf = CSRFProtect(app)
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[os.environ.get("GK_RATE_LIMIT_DEFAULT", "600/minute")],
        storage_uri="memory://",
    )

    @app.after_request
    def _headers(resp):
        return security.add_security_headers(resp)

    def _absolute_url(endpoint: str, **kwargs) -> str:
        """URL bilden und auf LAN-IP normalisieren (nicht localhost)."""
        rel = url_for(endpoint, **kwargs)
        return network.public_base_url() + rel

    def _qr_png(payload: str, download_name: str | None = None):
        import qrcode
        buf = io.BytesIO()
        qrcode.make(payload).save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png",
                         download_name=download_name or "qr.png")

    @app.context_processor
    def _inject():
        return {
            "csrf_token": generate_csrf,
            "default_password": admin_auth.is_default_password(),
            "url_map_endpoints": {r.endpoint for r in app.url_map.iter_rules()},
            "absolute_url": _absolute_url,
            "public_base_url": network.public_base_url,
        }

    PER_PAGE = 25

    def login_required(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                return redirect(url_for("login"))
            return func(*args, **kwargs)
        return wrapper

    def role_required(minimum: str):
        def deco(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if not session.get("user"):
                    return redirect(url_for("login"))
                if not admins_mod.has_permission(_current_role(), minimum):
                    abort(403)
                return func(*args, **kwargs)
            return wrapper
        return deco

    # ------------------------------------------------------------------ Auth
    @app.route("/login", methods=["GET", "POST"])
    @limiter.limit(os.environ.get("GK_RATE_LIMIT_LOGIN", "5/minute"), methods=["POST"])
    def login():
        error: str | None = None
        needs_totp = False
        if request.method == "POST":
            user = (request.form.get("username") or "").strip()
            pw = request.form.get("password") or ""
            totp = (request.form.get("totp") or "").strip()

            # 1) Datei-basierter Fallback-Admin (superadmin).
            expected = os.environ.get("GK_ADMIN_USER", "admin")
            if user == expected and admin_auth.verify_password(pw):
                session.clear()
                session.update(user=user, role="superadmin")
                session.permanent = True
                _audit("login.success", target=user, details="file-admin")
                return redirect(url_for("index"))

            # 2) DB-Admin.
            admin = admins_mod.verify(user, pw)
            if admin:
                if admin.totp_enabled:
                    secret = admins_mod.get_totp_secret(admin.id)
                    if not totp:
                        needs_totp = True
                        error = "Bitte 2FA-Code eingeben"
                    elif not admins_mod.verify_totp(secret or "", totp):
                        error = "Ungültiger 2FA-Code"
                        _audit("login.totp_failed", target=user)
                    else:
                        session.clear()
                        session.update(user=admin.username, role=admin.role, admin_id=admin.id)
                        session.permanent = True
                        _audit("login.success", target=admin.username, details=admin.role)
                        return redirect(url_for("index"))
                else:
                    session.clear()
                    session.update(user=admin.username, role=admin.role, admin_id=admin.id)
                    session.permanent = True
                    _audit("login.success", target=admin.username, details=admin.role)
                    return redirect(url_for("index"))
            else:
                if not needs_totp:
                    _audit("login.failed", target=user or "?")
                    error = error or "Falsche Zugangsdaten"
        return render_template("login.html", error=error, needs_totp=needs_totp)

    @app.route("/login/rfid", methods=["POST"])
    @limiter.limit("10/minute")
    def login_rfid():
        uid = (request.form.get("uid") or "").strip() or (request.json or {}).get("uid", "")
        admin = admins_mod.get_by_uid(uid)
        if not admin:
            return jsonify({"ok": False, "error": "Karte unbekannt"}), 401
        session.clear()
        session.update(user=admin.username, role=admin.role, admin_id=admin.id)
        session.permanent = True
        _audit("login.rfid", target=admin.username)
        return jsonify({"ok": True, "next": url_for("index")})

    @app.route("/logout")
    def logout():
        if _current_user():
            _audit("logout")
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------ Health
    @app.get("/healthz")
    @csrf.exempt
    def healthz():
        try:
            with database.get_connection() as conn:
                conn.execute("SELECT 1")
            return jsonify({"status": "ok", "version": "3.0",
                            "schema": migrations.current_version()})
        except sqlite3.Error as e:
            return jsonify({"status": "error", "detail": str(e)}), 500

    # ------------------------------------------------------------------ RFID API + Live
    @app.route("/read_uid")
    @login_required
    def read_uid():
        uid = models.rfid_read_for_web()
        if uid is None:
            return jsonify({"uid": "", "error": "RFID-Reader nicht verfügbar"})
        return jsonify({"uid": uid})

    @app.route("/user_name")
    @login_required
    def user_name():
        uid = request.args.get("uid")
        name = ""
        if uid:
            user = models.get_user_by_uid(uid)
            if user:
                name = user.name
        return jsonify({"name": name})

    @app.get("/events/rfid")
    @csrf.exempt
    def rfid_events():
        return Response(
            stream_with_context(rfid_bridge.sse_stream()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    _LAST_ACT_ID = {"id": 0}

    @app.get("/events/activity")
    @csrf.exempt
    def activity_events():
        def gen():
            last = _LAST_ACT_ID["id"]
            while True:
                with database.get_connection() as conn:
                    try:
                        rows = conn.execute(
                            "SELECT id, timestamp, kind, actor, target, amount_cents "
                            "FROM activity_log WHERE id > ? ORDER BY id LIMIT 50",
                            (last,),
                        ).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
                for r in rows:
                    last = int(r["id"])
                    yield f"event: activity\ndata: {json.dumps(dict(r))}\n\n".encode()
                _LAST_ACT_ID["id"] = last
                time.sleep(2.0)
                yield b": keepalive\n\n"

        return Response(
            stream_with_context(gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------ Palette
    @app.get("/api/palette")
    @login_required
    def palette():
        items: list[dict] = [
            {"kind": "Aktion", "title": "Neues Getränk", "href": url_for("drinks")},
            {"kind": "Aktion", "title": "Neuen Nutzer anlegen", "href": url_for("users")},
            {"kind": "Aktion", "title": "Guthaben aufladen", "href": url_for("topup")},
            {"kind": "Aktion", "title": "Backup jetzt", "href": url_for("backup_view")},
            {"kind": "Aktion", "title": "Kassensturz starten", "href": url_for("kassensturz_view")},
            {"kind": "Aktion", "title": "Inventur starten", "href": url_for("inventur_view")},
            {"kind": "Aktion", "title": "Rabatt anlegen", "href": url_for("discount_list")},
            {"kind": "Aktion", "title": "Update installieren", "href": url_for("system_update_view")},
            {"kind": "Seite", "title": "Dashboard", "href": url_for("dashboard")},
            {"kind": "Seite", "title": "Kasse öffnen", "href": url_for("kiosk_view")},
            {"kind": "Seite", "title": "Audit-Log", "href": url_for("audit_view")},
            {"kind": "Seite", "title": "Admins", "href": url_for("admins_view")},
            {"kind": "Seite", "title": "Webhooks", "href": url_for("webhook_view")},
        ]
        with database.get_connection() as conn:
            drinks = conn.execute(
                "SELECT id, name, price FROM drinks WHERE deleted_at IS NULL ORDER BY name"
            ).fetchall()
            users = conn.execute(
                "SELECT id, name FROM users WHERE deleted_at IS NULL AND is_event=0 ORDER BY name LIMIT 100"
            ).fetchall()
        for d in drinks:
            items.append({"kind": "Getränk", "title": d["name"],
                          "subtitle": f"{d['price']/100:.2f} €",
                          "href": url_for("drink_edit", drink_id=d["id"])})
        for u in users:
            items.append({"kind": "Nutzer", "title": u["name"],
                          "href": url_for("user_edit", user_id=u["id"])})
        return jsonify({"items": items})

    # ------------------------------------------------------------------ Home
    @app.route("/", methods=["GET"])
    def index():
        if not session.get("user"):
            return redirect(url_for("login"))
        conn = database.get_connection()
        to_buy = models.get_drinks_below_min(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) AS total FROM users WHERE is_event=0 AND (deleted_at IS NULL)"
        ).fetchone()
        total_balance = row["total"] if row else 0
        conn.close()
        recommendations = models.get_purchase_recommendations(
            days=30, coverage_days=21, replenish_cycle_days=45
        )
        recent = activity.recent(15)
        return render_template("index.html", to_buy=to_buy, total_balance=total_balance,
                               recommendations=recommendations[:5], recent=recent)

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats, totals = models.get_monthly_stats()
        recent = activity.recent(20)
        return render_template("dashboard.html", stats=stats, totals=totals, recent=recent)

    @app.get("/api/stats/daily")
    @login_required
    def api_daily():
        days = max(7, min(365, request.args.get("days", type=int, default=30)))
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT DATE(t.timestamp) AS day, "
                "SUM(CASE WHEN u.name='BARZAHLUNG' THEN t.quantity * d.price ELSE 0 END) AS cash, "
                "SUM(CASE WHEN u.name!='BARZAHLUNG' THEN t.quantity * d.price ELSE 0 END) AS card "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "JOIN users u ON u.id=t.user_id "
                "WHERE DATE(t.timestamp) >= DATE('now', ?) "
                "GROUP BY day ORDER BY day",
                (f"-{days} day",),
            ).fetchall()
            topups = conn.execute(
                "SELECT DATE(timestamp) AS day, SUM(amount) AS topup "
                "FROM topups WHERE DATE(timestamp) >= DATE('now', ?) "
                "GROUP BY day ORDER BY day",
                (f"-{days} day",),
            ).fetchall()
        topup_map = {r["day"]: int(r["topup"] or 0) for r in topups}
        return jsonify([
            {"day": r["day"], "cash": int(r["cash"] or 0),
             "card": int(r["card"] or 0), "topup": topup_map.get(r["day"], 0)}
            for r in rows
        ])

    @app.get("/api/stats/top_drinks")
    @login_required
    def api_top_drinks():
        days = max(1, min(365, request.args.get("days", type=int, default=30)))
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT d.name AS name, COALESCE(SUM(t.quantity), 0) AS quantity "
                "FROM drinks d LEFT JOIN transactions t ON t.drink_id=d.id "
                "AND DATE(t.timestamp) >= DATE('now', ?) "
                "GROUP BY d.id HAVING quantity > 0 ORDER BY quantity DESC LIMIT 10",
                (f"-{days} day",),
            ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.route("/reports")
    @login_required
    def reports():
        period = request.args.get("period", default="month", type=str)
        if period not in {"day", "week", "month"}:
            period = "month"
        date_clause = _date_filter_clause(period)
        conn = database.get_connection()
        top_articles = conn.execute(
            "SELECT d.name AS drink_name, SUM(t.quantity) AS quantity, "
            "SUM(t.quantity * d.price) AS revenue "
            "FROM transactions t JOIN drinks d ON d.id = t.drink_id "
            f"WHERE DATE(t.timestamp) >= {date_clause} "
            "GROUP BY d.id ORDER BY quantity DESC, revenue DESC LIMIT 10"
        ).fetchall()
        topups = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS count "
            f"FROM topups WHERE DATE(timestamp) >= {date_clause}"
        ).fetchone()
        stock = conn.execute(
            "SELECT name, stock, min_stock, "
            "CASE WHEN min_stock > 0 THEN ROUND(stock * 1.0 / min_stock, 2) ELSE NULL END AS ratio "
            "FROM drinks WHERE deleted_at IS NULL ORDER BY stock ASC, name"
        ).fetchall()
        conn.close()
        recommendations = models.get_purchase_recommendations(days=30, coverage_days=21, replenish_cycle_days=45)
        forecast = []
        for row in stock:
            status = ("kritisch" if row["stock"] <= 0
                      else ("niedrig" if row["stock"] < row["min_stock"] else "ok"))
            forecast.append({"name": row["name"], "stock": row["stock"],
                             "min_stock": row["min_stock"], "ratio": row["ratio"], "status": status})
        return render_template("reports.html", period=period, top_articles=top_articles,
                               topups=topups, forecast=forecast, recommendations=recommendations)

    @app.route("/einkaufen")
    @login_required
    def einkaufen():
        days = request.args.get("days", default=30, type=int)
        recs = models.get_purchase_recommendations(days=days, coverage_days=21, replenish_cycle_days=max(45, days))
        recs = sorted(recs, key=lambda r: (-r["buy_qty"], -r["sold"], r["name"].lower()))
        return render_template("shopping.html", days=days, recommendations=recs)

    @app.route("/dashboard/receipt")
    @login_required
    def dashboard_receipt():
        stats, totals = models.get_monthly_stats()
        return render_template("dashboard_receipt.html", stats=stats, totals=totals)

    # ------------------------------------------------------------------ Kiosk
    @app.route("/kiosk")
    def kiosk_view():
        conn = database.get_connection()
        categories = conn.execute(
            "SELECT id, name, color, sort_order FROM drink_categories ORDER BY sort_order, name"
        ).fetchall()
        rows = conn.execute(
            "SELECT id, name, price, stock, min_stock, category_id, image "
            "FROM drinks WHERE deleted_at IS NULL ORDER BY name"
        ).fetchall()
        conn.close()
        drinks = []
        for r in rows:
            eff = discounts.effective_price(r["price"], r["id"])
            disc = discounts.active_discount_for(r["id"])
            drinks.append({
                "id": r["id"], "name": r["name"], "price": r["price"], "stock": r["stock"],
                "min_stock": r["min_stock"], "category_id": r["category_id"],
                "effective": eff,
                "discount_percent": disc.percent if disc else 0,
            })
        # Attract-Mode-Zeilen: derzeit Top-Getränke der letzten 7 Tage
        with database.get_connection() as conn:
            try:
                top = conn.execute(
                    "SELECT d.name, SUM(t.quantity) q FROM transactions t "
                    "JOIN drinks d ON d.id=t.drink_id "
                    "WHERE DATE(t.timestamp) >= DATE('now','-7 day') "
                    "GROUP BY d.id ORDER BY q DESC LIMIT 5"
                ).fetchall()
            except sqlite3.OperationalError:
                top = []
        attract_lines = [f"Top: {r['name']} ({r['q']}×)" for r in top]
        # Aktive Veranstaltungs-„Schnellwahl"-Karten (wie im Bestand: Name + Saldo).
        events = []
        for u in models.get_event_payment_users():
            if not u.rfid_uid:
                continue
            # In der Bestands-Logik wird der aufsummierte Verbrauch angezeigt.
            with database.get_connection() as _c:
                spent = _c.execute(
                    "SELECT COALESCE(SUM(t.quantity * d.price), 0) AS s "
                    "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                    "WHERE t.user_id=?", (u.id,),
                ).fetchone()["s"] or 0
            events.append({"uid": u.rfid_uid, "name": u.name, "spent": int(spent)})
        return render_template(
            "kiosk.html", drinks=drinks, categories=categories,
            attract_lines=attract_lines,
            event_cards=events,
            dev_mode=os.environ.get("GK_DEV") == "1",
            weather_lat=float(os.environ.get("GK_LAT") or 0),
            weather_lon=float(os.environ.get("GK_LON") or 0),
        )

    @app.post("/api/kiosk/simulate")
    @csrf.exempt
    def kiosk_simulate():
        data = request.get_json(silent=True) or {}
        uid = str(data.get("uid") or "").strip()
        if not uid:
            return jsonify({"ok": False}), 400
        if os.environ.get("GK_DEV") != "1":
            return jsonify({"ok": False, "error": "GK_DEV nicht aktiv"}), 403
        rfid_bridge.push_uid(uid)
        return jsonify({"ok": True})

    @app.post("/api/kiosk/book")
    @csrf.exempt
    def kiosk_book():
        data = request.get_json(silent=True) or {}
        uid = str(data.get("uid") or "").strip()
        items_raw = data.get("items") or []
        cart = [kiosk_mod.LineItem(int(i["drink_id"]), int(i["quantity"]))
                for i in items_raw if i.get("drink_id") and int(i.get("quantity") or 0) > 0]
        if not cart:
            return jsonify({"ok": False, "error": "Warenkorb ist leer"}), 400
        res = kiosk_mod.book(uid, cart)
        payload = {"ok": res.ok, "user_name": res.user_name, "total_cents": res.total_cents,
                   "new_balance_cents": res.new_balance_cents, "error": res.error,
                   "receipt_ref": res.receipt_ref}
        # Self-Service-Link nur für Guthaben-Karten (kein Bar, kein Event).
        if res.ok and uid and uid.upper() != "CASH":
            user = models.get_user_by_uid(uid)
            if user and not user.is_event:
                payload["selfservice_url"] = _absolute_url(
                    "selfservice.show", token=make_token(uid)
                )
        return jsonify(payload), (200 if res.ok else 400)

    @app.get("/api/kiosk/recent")
    @csrf.exempt
    def kiosk_recent():
        return jsonify(kiosk_mod.recent_receipts(limit=5))

    @app.get("/api/kiosk/card/<uid>")
    @csrf.exempt
    def kiosk_card_info(uid: str):
        """Info zu einer Karte: bekannt/unbekannt, Guthaben, Rolle, Topup-Karte?"""
        uid = uid.strip()
        topup_uid = models.get_topup_card()
        if uid and topup_uid and uid == topup_uid:
            return jsonify({"kind": "topup", "uid": uid})
        user = models.get_user_by_uid(uid)
        if user:
            payload = {
                "kind": "event" if user.is_event else "user",
                "uid": uid, "name": user.name,
                "balance_cents": user.balance,
            }
            # Self-Service-Link: nur für normale Guthaben-Nutzer (kein Event, kein CASH).
            if not user.is_event and uid.upper() != "CASH":
                token = make_token(uid)
                payload["selfservice_url"] = _absolute_url(
                    "selfservice.show", token=token
                )
            return jsonify(payload)
        # unbekannt: gibt es wartende Nutzer ohne UID?
        pending = models.get_unassigned_users(limit=20)
        return jsonify({
            "kind": "unknown", "uid": uid,
            "pending": [{"id": u.id, "name": u.name} for u in pending],
        })

    @app.post("/api/kiosk/topup")
    @csrf.exempt
    def kiosk_topup():
        data = request.get_json(silent=True) or {}
        uid = str(data.get("uid") or "").strip()
        amount_cents = int(data.get("amount_cents") or 0)
        pin = str(data.get("pin") or "").strip()
        if not uid or amount_cents <= 0:
            return jsonify({"ok": False, "error": "bad_request"}), 400
        # PIN oder Aufladekarte muss vorliegen (Missbrauchsschutz)
        expected = models.get_admin_pin()
        buyer_pin = models.get_buyer_pin()
        via_card = str(data.get("via_topup_card") or "") == "1"
        if not via_card and pin not in {expected, buyer_pin}:
            return jsonify({"ok": False, "error": "PIN erforderlich"}), 401
        user = models.get_user_by_uid(uid)
        if not user:
            return jsonify({"ok": False, "error": "Karte unbekannt"}), 404
        models.update_balance(user.id, amount_cents)
        models.add_topup(user.id, amount_cents)
        ledger.on_cash_topup(amount_cents, ref=f"kiosk topup user={user.id}",
                             actor="kiosk")
        _audit("topup", target=f"user:{user.id}",
               details=f"kiosk amount_cents={amount_cents}")
        _act("topup", target=user.name, amount_cents=amount_cents)
        fresh = models.get_user(user.id)
        return jsonify({"ok": True, "user_name": user.name,
                        "balance_cents": fresh.balance if fresh else user.balance})

    @app.post("/api/kiosk/pin_check")
    @csrf.exempt
    def kiosk_pin_check():
        data = request.get_json(silent=True) or {}
        pin = str(data.get("pin") or "").strip()
        expected_admin = models.get_admin_pin()
        expected_buyer = models.get_buyer_pin()
        if pin == expected_admin:
            return jsonify({"ok": True, "role": "admin"})
        if pin == expected_buyer:
            return jsonify({"ok": True, "role": "buyer"})
        return jsonify({"ok": False}), 401

    @app.post("/api/kiosk/assign_card")
    @csrf.exempt
    def kiosk_assign_card():
        data = request.get_json(silent=True) or {}
        user_id = int(data.get("user_id") or 0)
        uid = str(data.get("uid") or "").strip()
        pin = str(data.get("pin") or "").strip()
        expected = models.get_admin_pin()
        buyer = models.get_buyer_pin()
        if pin not in {expected, buyer}:
            return jsonify({"ok": False, "error": "PIN erforderlich"}), 401
        if models.assign_rfid_to_user(user_id, uid):
            _audit("card.assign", target=str(user_id), details=uid)
            _act("card.assign", target=str(user_id), note=uid)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Zuordnung fehlgeschlagen"}), 400

    @app.post("/api/kiosk/reverse/<ref>")
    @csrf.exempt
    def kiosk_reverse(ref: str):
        data = request.get_json(silent=True) or {}
        pin = str(data.get("pin") or "").strip()
        expected = models.get_admin_pin()
        if pin != expected:
            return jsonify({"ok": False, "error": "PIN falsch"}), 401
        res = kiosk_mod.reverse_booking(ref, actor="kiosk")
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.get("/kassenbon/<ref>.pdf")
    def kassenbon_pdf(ref: str):
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT t.timestamp, t.quantity, d.name AS drink, d.price, u.name AS user_name "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "JOIN users u ON u.id=t.user_id "
                "WHERE t.receipt_ref=? ORDER BY t.id",
                (ref,),
            ).fetchall()
        if not rows:
            abort(404)
        total = sum(r["quantity"] * r["price"] for r in rows)
        # 58 mm Bon (~ 210 mm hoch max)
        pdf = FPDF(orientation="P", unit="mm", format=(58, 200))
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 6, "GETRAENKEKASSE", ln=1, align="C")
        pdf.set_font("Helvetica", size=8)
        pdf.cell(0, 4, rows[0]["timestamp"][:16], ln=1, align="C")
        pdf.cell(0, 4, f"Kunde: {rows[0]['user_name']}", ln=1, align="C")
        pdf.cell(0, 4, "-" * 32, ln=1, align="C")
        pdf.set_font("Helvetica", size=9)
        for r in rows:
            label = r["drink"][:20]
            line_total = r["quantity"] * r["price"] / 100
            pdf.cell(30, 5, f"{r['quantity']}x {label}")
            pdf.cell(0, 5, f"{line_total:>6.2f}", ln=1, align="R")
        pdf.cell(0, 3, "-" * 32, ln=1, align="C")
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(30, 6, "Summe")
        pdf.cell(0, 6, f"{total/100:>6.2f} EUR", ln=1, align="R")
        pdf.ln(2)
        pdf.set_font("Helvetica", size=7)
        pdf.cell(0, 3, "Kein Umtausch. Danke!", ln=1, align="C")
        pdf.cell(0, 3, f"Ref: {ref}", ln=1, align="C")
        return send_file(io.BytesIO(pdf.output()),
                         mimetype="application/pdf",
                         download_name=f"kassenbon_{ref}.pdf")

    # ------------------------------------------------------------------ Steuer
    @app.route("/refresh", methods=["POST"])
    @login_required
    def refresh():
        database.touch_refresh_flag()
        _audit("gui.refresh")
        return redirect(url_for("index"))

    @app.route("/stop", methods=["POST"])
    @role_required("admin")
    def stop():
        database.set_exit_flag()
        _audit("gui.stop")
        return "Beende Anwendung..."

    # ------------------------------------------------------------------ Passwort
    @app.route("/password", methods=["GET", "POST"])
    @login_required
    def change_password():
        error: str | None = None
        info: str | None = None
        if request.method == "POST":
            old_pw = request.form.get("old_pw") or ""
            new_pw1 = request.form.get("new_pw1") or ""
            new_pw2 = request.form.get("new_pw2") or ""
            if not admin_auth.verify_password(old_pw):
                error = "Altes Passwort falsch"
            elif len(new_pw1) < 8:
                error = "Neues Passwort muss mindestens 8 Zeichen haben"
            elif new_pw1 != new_pw2:
                error = "Passwörter stimmen nicht überein"
            elif new_pw1 == admin_auth.DEFAULT_PASSWORD:
                error = "Standardpasswort ist nicht erlaubt"
            else:
                admin_auth.set_password(new_pw1)
                _audit("password.change")
                info = "Passwort gespeichert"
        return render_template("change_password.html", error=error, info=info)

    # ------------------------------------------------------------------ Admins (DB)
    @app.route("/admins")
    @role_required("superadmin")
    def admins_view():
        return render_template("admins.html",
                               admins=admins_mod.list_admins(), roles=admins_mod.ROLES)

    @app.route("/admins/create", methods=["POST"])
    @role_required("superadmin")
    def admin_create():
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        role = request.form.get("role") or "admin"
        try:
            admins_mod.create(username, password, role)
            _audit("admin.create", target=username, details=role)
        except sqlite3.IntegrityError:
            pass
        return redirect(url_for("admins_view"))

    @app.route("/admins/<int:admin_id>/edit")
    @role_required("superadmin")
    def admin_edit_view(admin_id: int):
        admin = next((a for a in admins_mod.list_admins() if a.id == admin_id), None)
        if not admin:
            abort(404)
        new_secret = session.pop("new_totp_secret", None)
        return render_template("admin_edit.html", admin=admin, new_totp_secret=new_secret)

    @app.route("/admins/<int:admin_id>/password", methods=["POST"])
    @role_required("superadmin")
    def admin_set_password(admin_id: int):
        pw = request.form.get("password") or ""
        if len(pw) >= 8:
            admins_mod.set_password(admin_id, pw)
            _audit("admin.password", target=str(admin_id))
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.route("/admins/<int:admin_id>/role", methods=["POST"])
    @role_required("superadmin")
    def admin_set_role(admin_id: int):
        role = request.form.get("role") or "admin"
        try:
            admins_mod.set_role(admin_id, role)
            _audit("admin.role", target=str(admin_id), details=role)
        except ValueError:
            pass
        return redirect(url_for("admins_view"))

    @app.route("/admins/<int:admin_id>/toggle", methods=["POST"])
    @role_required("superadmin")
    def admin_toggle_active(admin_id: int):
        active = request.form.get("active") == "1"
        admins_mod.set_active(admin_id, active)
        _audit("admin.toggle", target=str(admin_id), details=str(active))
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.route("/admins/<int:admin_id>/rfid", methods=["POST"])
    @role_required("superadmin")
    def admin_bind_rfid(admin_id: int):
        uid = (request.form.get("uid") or "").strip()
        admins_mod.bind_rfid(admin_id, uid or None)
        _audit("admin.rfid", target=str(admin_id), details=uid or "cleared")
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.route("/admins/<int:admin_id>/delete", methods=["POST"])
    @role_required("superadmin")
    def admin_delete(admin_id: int):
        admins_mod.delete(admin_id)
        _audit("admin.delete", target=str(admin_id))
        return redirect(url_for("admins_view"))

    @app.route("/admins/<int:admin_id>/totp/start", methods=["POST"])
    @role_required("superadmin")
    def admin_totp_start(admin_id: int):
        session["new_totp_secret"] = admins_mod.generate_totp_secret()
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.route("/admins/<int:admin_id>/totp/confirm", methods=["POST"])
    @role_required("superadmin")
    def admin_totp_confirm(admin_id: int):
        secret = request.form.get("secret") or ""
        code = request.form.get("code") or ""
        if admins_mod.verify_totp(secret, code):
            admins_mod.enable_totp(admin_id, secret)
            _audit("admin.totp_enabled", target=str(admin_id))
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.route("/admins/<int:admin_id>/totp/disable", methods=["POST"])
    @role_required("superadmin")
    def admin_totp_disable(admin_id: int):
        admins_mod.disable_totp(admin_id)
        _audit("admin.totp_disabled", target=str(admin_id))
        return redirect(url_for("admin_edit_view", admin_id=admin_id))

    @app.get("/admins/<int:admin_id>/totp/qr.png")
    @role_required("superadmin")
    def admin_totp_qr(admin_id: int):
        secret = request.args.get("secret") or ""
        admin = next((a for a in admins_mod.list_admins() if a.id == admin_id), None)
        if not admin or not secret:
            abort(404)
        uri = admins_mod.totp_uri(admin.username, secret)
        import qrcode
        buf = io.BytesIO()
        qrcode.make(uri).save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    # ------------------------------------------------------------------ Settings
    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        conn = database.get_connection()
        current_limit = models.get_overdraft_limit(conn)
        current_pin = models.get_admin_pin(conn)
        current_buyer_pin = models.get_buyer_pin(conn)
        current_game_enabled = models.is_game_enabled(conn)
        current_free_day = models.is_free_day_enabled(conn)
        current_topup_card = models.get_topup_card(conn)
        data_dir = Path(__file__).resolve().parent.parent / "data"
        qr_path = data_dir / "web_qr.png"
        bg_path = data_dir / "background.png"
        thank_path = data_dir / "background_thanks.png"
        free_path = data_dir / "background_free.png"
        if request.method == "POST":
            val = request.form.get("overdraft", type=float)
            if val is not None:
                models.set_overdraft_limit(int(val * 100), conn)
            pin_val = request.form.get("admin_pin")
            buyer_pin_val = request.form.get("buyer_pin")
            if pin_val is not None:
                models.set_admin_pin(pin_val, conn)
                current_pin = pin_val
            if buyer_pin_val is not None and buyer_pin_val != current_pin:
                models.set_buyer_pin(buyer_pin_val, conn)
                current_buyer_pin = buyer_pin_val
            game_val = request.form.get("game_enabled")
            models.set_game_enabled(bool(game_val), conn)
            current_game_enabled = bool(game_val)
            free_val = request.form.get("free_day_enabled")
            models.set_free_day_enabled(bool(free_val), conn)
            current_free_day = bool(free_val)
            topup_card_val = request.form.get("topup_card_uid")
            if topup_card_val is not None:
                models.set_topup_card(topup_card_val.strip(), conn)
                current_topup_card = topup_card_val.strip()

            def _save_upload(field: str, dest: Path) -> None:
                fh = request.files.get(field)
                if not fh or not fh.filename:
                    return
                new_name = security.safe_upload_name(fh.filename)
                if not new_name:
                    return
                data_dir.mkdir(parents=True, exist_ok=True)
                fh.save(dest)
                _audit("settings.upload", target=field)

            _save_upload("qr_code", qr_path)
            _save_upload("background", bg_path)
            _save_upload("thank_background", thank_path)
            _save_upload("free_background", free_path)
            database.touch_refresh_flag()
            _audit("settings.update")
            conn.close()
            return redirect(url_for("settings"))
        conn.close()
        return render_template("settings.html",
                               overdraft_limit=current_limit,
                               admin_pin=current_pin, buyer_pin=current_buyer_pin,
                               game_enabled=current_game_enabled,
                               free_day_enabled=current_free_day,
                               topup_card_uid=current_topup_card,
                               qr_code_exists=qr_path.exists(),
                               background_exists=bg_path.exists(),
                               thank_background_exists=thank_path.exists(),
                               free_background_exists=free_path.exists(),
                               error=None)

    # ------------------------------------------------------------------ Telegram
    @app.route("/telegram", methods=["GET", "POST"])
    @login_required
    def telegram():
        info: str | None = None
        if request.method == "POST":
            action = request.form.get("action") or "save"
            if action == "publish":
                notifier.reload_settings()
                notifier.send_status()
                info = "Status gesendet"
            else:
                token = request.form.get("token") or ""
                chat_id = request.form.get("chat_id") or ""
                models.set_telegram_token(token)
                models.set_telegram_chat(chat_id)
                notifier.reload_settings()
                notifier.start()
                info = "Gespeichert"
        token = models.get_telegram_token()
        chat_id = models.get_telegram_chat()
        return render_template("telegram.html", token=token, chat_id=chat_id, info=info)

    # ------------------------------------------------------------------ Static / Images
    def _send_data(name: str, filename: str):
        path = Path(__file__).resolve().parent.parent / "data" / filename
        return send_file(path) if path.exists() else ("", 404)

    @app.route("/web_qr.png")
    @login_required
    def web_qr_png(): return _send_data("qr", "web_qr.png")

    @app.route("/background.png")
    @login_required
    def background_png(): return _send_data("bg", "background.png")

    @app.route("/thank_background.png")
    @login_required
    def thank_background_png(): return _send_data("thank", "background_thanks.png")

    @app.route("/free_background.png")
    @login_required
    def free_background_png(): return _send_data("free", "background_free.png")

    # ------------------------------------------------------------------ Drinks
    @app.route("/drinks")
    @login_required
    def drinks(error: str | None = None):
        conn = database.get_connection()
        items = conn.execute(
            "SELECT d.*, c.name AS cat_name FROM drinks d "
            "LEFT JOIN drink_categories c ON c.id=d.category_id "
            "WHERE d.deleted_at IS NULL ORDER BY d.name"
        ).fetchall()
        categories = conn.execute(
            "SELECT id, name FROM drink_categories ORDER BY sort_order, name"
        ).fetchall()
        conn.close()
        return render_template("drinks.html", drinks=items, categories=categories, error=error)

    @app.route("/drinks/add", methods=["POST"])
    @login_required
    def drink_add():
        name = (request.form.get("name") or "").strip()
        price_euro = request.form.get("price", type=float)
        stock = request.form.get("stock", type=int)
        min_stock = request.form.get("min_stock", type=int)
        page = request.form.get("page", type=int) or 1
        cat = request.form.get("category_id", type=int)
        image_file = request.files.get("image")
        image_path = None
        if image_file and image_file.filename:
            new_name = security.safe_upload_name(image_file.filename)
            if not new_name:
                return drinks(error="Ungültiger Bildtyp")
            image_dir = Path(__file__).resolve().parent.parent / "data" / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            dest = image_dir / new_name
            image_file.save(dest)
            image_path = str(dest)
        if not name or price_euro is None:
            return drinks(error="Name und Preis erforderlich")
        price = int(price_euro * 100)
        with database.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM drinks WHERE page=? AND deleted_at IS NULL",
                (page,),
            ).fetchone()[0]
            if count >= 9:
                return drinks(error="Maximal 9 Getränke pro Seite erlaubt")
            barcode = (request.form.get("barcode") or "").strip() or None
            conn.execute(
                "INSERT INTO drinks (name, price, stock, min_stock, page, image, category_id, barcode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, price, stock or 0, min_stock or 0, page, image_path, cat, barcode),
            )
            conn.commit()
        database.touch_refresh_flag()
        _audit("drink.add", target=name)
        _act("drink.add", target=name)
        return redirect(url_for("drinks"))

    @app.route("/drinks/delete/<int:drink_id>", methods=["POST"])
    @login_required
    def drink_delete(drink_id: int):
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM drinks WHERE id=?", (drink_id,)
            ).fetchone()
            conn.execute(
                "UPDATE drinks SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (drink_id,)
            )
            conn.commit()
        database.touch_refresh_flag()
        _audit("drink.softdelete", target=str(drink_id))
        _act("drink.delete", target=row["name"] if row else str(drink_id))
        session["undo"] = {
            "kind": "drink",
            "id": drink_id,
            "label": row["name"] if row else "Getränk",
        }
        return redirect(url_for("drinks"))

    @app.route("/drinks/restore/<int:drink_id>", methods=["POST"])
    @login_required
    def drink_restore(drink_id: int):
        with database.get_connection() as conn:
            conn.execute("UPDATE drinks SET deleted_at=NULL WHERE id=?", (drink_id,))
            conn.commit()
        database.touch_refresh_flag()
        _audit("drink.restore", target=str(drink_id))
        return redirect(url_for("drinks"))

    # ---------------- Papierkorb ----------------
    @app.route("/trash")
    @login_required
    def trash_view():
        with database.get_connection() as conn:
            drinks_ = conn.execute(
                "SELECT id, name, deleted_at FROM drinks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
            ).fetchall()
            users_ = conn.execute(
                "SELECT id, name, rfid_uid, balance, deleted_at FROM users "
                "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
            ).fetchall()
        return render_template("trash.html", drinks=drinks_, users=users_)

    @app.route("/trash/user_restore/<int:user_id>", methods=["POST"])
    @login_required
    def user_restore(user_id: int):
        with database.get_connection() as conn:
            conn.execute("UPDATE users SET deleted_at=NULL, active=1 WHERE id=?", (user_id,))
            conn.commit()
        _audit("user.restore", target=str(user_id))
        return redirect(url_for("trash_view"))

    @app.route("/trash/drink_purge/<int:drink_id>", methods=["POST"])
    @role_required("admin")
    def drink_purge(drink_id: int):
        with database.get_connection() as conn:
            # Nur wenn keine Transaktionen mehr verweisen (Integrität).
            row = conn.execute(
                "SELECT COUNT(*) c FROM transactions WHERE drink_id=?", (drink_id,)
            ).fetchone()
            if row["c"] == 0:
                conn.execute("DELETE FROM drinks WHERE id=?", (drink_id,))
                conn.commit()
                _audit("drink.purge", target=str(drink_id))
            else:
                _audit("drink.purge_denied", target=str(drink_id),
                       details=f"{row['c']} transactions exist")
        return redirect(url_for("trash_view"))

    @app.route("/trash/user_purge/<int:user_id>", methods=["POST"])
    @role_required("admin")
    def user_purge(user_id: int):
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM transactions WHERE user_id=?", (user_id,)
            ).fetchone()
            if row["c"] == 0:
                conn.execute("DELETE FROM users WHERE id=?", (user_id,))
                conn.commit()
                _audit("user.purge", target=str(user_id))
            else:
                _audit("user.purge_denied", target=str(user_id),
                       details=f"{row['c']} transactions exist")
        return redirect(url_for("trash_view"))

    @app.route("/drinks/inline", methods=["POST"])
    @login_required
    def drink_inline():
        drink_id = request.form.get("id", type=int)
        field = request.form.get("field")
        value = request.form.get("value")
        if not drink_id or field not in {"price", "stock", "min_stock"}:
            abort(400)
        with database.get_connection() as conn:
            if field == "price":
                new_price = int(float(value) * 100)
                old = conn.execute(
                    "SELECT price, name FROM drinks WHERE id=?", (drink_id,)
                ).fetchone()
                conn.execute("UPDATE drinks SET price=? WHERE id=?", (new_price, drink_id))
                conn.execute(
                    "INSERT INTO price_history (drink_id, old_price, new_price, changed_by) "
                    "VALUES (?, ?, ?, ?)",
                    (drink_id, old["price"], new_price, _current_user()),
                )
                shown = f"{new_price/100:.2f} €"
                _act("price.change", target=old["name"] if old else str(drink_id),
                     amount_cents=new_price - (old["price"] if old else 0))
            else:
                iv = int(value)
                conn.execute(f"UPDATE drinks SET {field}=? WHERE id=?", (iv, drink_id))
                shown = str(iv)
            conn.commit()
        database.touch_refresh_flag()
        _audit("drink.inline", target=str(drink_id), details=f"{field}={value}")
        resp = make_response(shown)
        resp.headers[_hx_trigger({"showToast": {"msg": "Gespeichert", "level": "success", "duration": 2000}})[0]] = \
            _hx_trigger({"showToast": {"msg": "Gespeichert", "level": "success", "duration": 2000}})[1]
        return resp

    @app.route("/drinks/restock/<int:drink_id>", methods=["POST"])
    @login_required
    def drink_restock(drink_id: int):
        amount = request.form.get("amount", type=int)
        cost_euro = request.form.get("cost", type=float)  # optional
        receipt_file = request.files.get("receipt")
        if amount and amount > 0:
            with database.get_connection() as conn:
                row = conn.execute("SELECT stock, name FROM drinks WHERE id=?", (drink_id,)).fetchone()
                if row:
                    conn.execute("UPDATE drinks SET stock=? WHERE id=?", (row["stock"] + amount, drink_id))
                    conn.commit()
                    models.log_restock(drink_id, amount)
                    _act("restock", target=row["name"], note=f"+{amount}")
            database.touch_refresh_flag()
            _audit("drink.restock", target=str(drink_id), details=f"+{amount}")
            # optional als Ausgabe der Hauptkasse verbuchen
            if cost_euro and cost_euro > 0:
                receipt_path = ledger.save_receipt(receipt_file) if receipt_file else None
                try:
                    ledger.post(
                        "expense", int(round(cost_euro * 100)),
                        from_account="main_cash", to_account="expenses_other",
                        ref=f"restock drink={drink_id} +{amount}",
                        actor=_current_user(),
                        note=f"Einkauf: {amount}× {row['name']}",
                        receipt_path=receipt_path,
                    )
                except ValueError:
                    pass
        return redirect(url_for("drinks"))

    @app.route("/drinks/bulk_restock", methods=["POST"])
    @role_required("restocker")
    def drinks_bulk_restock():
        ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.strip().isdigit()]
        amount = request.form.get("amount", type=int) or 0
        if ids and amount > 0:
            with database.get_connection() as conn:
                for did in ids:
                    row = conn.execute("SELECT stock, name FROM drinks WHERE id=?", (did,)).fetchone()
                    if not row:
                        continue
                    conn.execute("UPDATE drinks SET stock=? WHERE id=?", (row["stock"] + amount, did))
                    conn.execute(
                        "INSERT INTO restocks (drink_id, quantity) VALUES (?, ?)",
                        (did, amount),
                    )
                    _act("restock", target=row["name"], note=f"+{amount}")
                conn.commit()
            database.touch_refresh_flag()
            _audit("drink.bulk_restock", details=f"ids={ids} +{amount}")
        return redirect(url_for("drinks"))

    # ---------------- Einkaufsmodus ----------------
    @app.route("/einkauf", methods=["GET", "POST"])
    @role_required("restocker")
    def einkauf_view():
        with database.get_connection() as conn:
            drinks_ = conn.execute(
                "SELECT id, name, stock, min_stock FROM drinks "
                "WHERE deleted_at IS NULL ORDER BY name"
            ).fetchall()
        if request.method == "POST":
            note = (request.form.get("note") or "Einkauf").strip()
            receipt_path = ledger.save_receipt(request.files.get("receipt")) or None
            total_cost = 0
            summary: list[str] = []
            with database.get_connection() as conn:
                for d in drinks_:
                    qty = request.form.get(f"qty_{d['id']}", type=int) or 0
                    cost = request.form.get(f"cost_{d['id']}", type=float) or 0.0
                    if qty <= 0:
                        continue
                    conn.execute(
                        "UPDATE drinks SET stock=? WHERE id=?",
                        (d["stock"] + qty, d["id"]),
                    )
                    conn.execute(
                        "INSERT INTO restocks (drink_id, quantity) VALUES (?, ?)",
                        (d["id"], qty),
                    )
                    total_cost += int(round(cost * 100))
                    summary.append(f"{qty}× {d['name']}")
                    _act("restock", target=d["name"], note=f"+{qty}")
                conn.commit()
            if total_cost > 0:
                try:
                    ledger.post(
                        "expense", total_cost,
                        from_account="main_cash", to_account="expenses_other",
                        ref="einkauf", actor=_current_user(),
                        note=f"Einkauf: {', '.join(summary)} — {note}",
                        receipt_path=receipt_path,
                    )
                except ValueError:
                    pass
            database.touch_refresh_flag()
            _audit("einkauf", details=f"{len(summary)} Positionen · {total_cost}ct")
            return redirect(url_for("einkauf_view"))
        recent_expenses = ledger.entries(kind="expense", limit=10)
        return render_template(
            "einkauf.html",
            drinks=drinks_,
            recent=recent_expenses,
        )

    @app.route("/drinks/<int:drink_id>/details")
    @login_required
    def drink_details(drink_id: int):
        with database.get_connection() as conn:
            d = conn.execute("SELECT * FROM drinks WHERE id=?", (drink_id,)).fetchone()
            if not d:
                abort(404)
            daily = conn.execute(
                "SELECT DATE(t.timestamp) AS day, SUM(t.quantity) AS q, "
                "SUM(t.quantity * d.price) AS rev "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "WHERE t.drink_id=? AND DATE(t.timestamp) >= DATE('now','-90 day') "
                "GROUP BY day ORDER BY day",
                (drink_id,),
            ).fetchall()
            total_qty = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS s FROM transactions WHERE drink_id=?",
                (drink_id,),
            ).fetchone()["s"]
            restocks = conn.execute(
                "SELECT timestamp, quantity FROM restocks WHERE drink_id=? "
                "ORDER BY id DESC LIMIT 25",
                (drink_id,),
            ).fetchall()
            top_buyers = conn.execute(
                "SELECT u.name AS name, SUM(t.quantity) AS q "
                "FROM transactions t JOIN users u ON u.id=t.user_id "
                "WHERE t.drink_id=? GROUP BY u.id ORDER BY q DESC LIMIT 5",
                (drink_id,),
            ).fetchall()
        active_disc = discounts.active_discount_for(drink_id)
        return render_template("drink_details.html",
                               drink=d, daily=[dict(r) for r in daily],
                               total_qty=int(total_qty or 0),
                               restocks=restocks, top_buyers=top_buyers,
                               active_discount=active_disc)

    @app.route("/drinks/<int:drink_id>/history")
    @login_required
    def drink_history(drink_id: int):
        with database.get_connection() as conn:
            drink = conn.execute("SELECT id, name FROM drinks WHERE id=?", (drink_id,)).fetchone()
            if not drink:
                abort(404)
            rows = conn.execute(
                "SELECT old_price, new_price, changed_at, changed_by FROM price_history "
                "WHERE drink_id=? ORDER BY id DESC",
                (drink_id,),
            ).fetchall()
        return render_template("drink_history.html", drink=drink, entries=rows)

    @app.get("/api/kiosk/scan/<code>")
    @csrf.exempt
    def kiosk_scan(code: str):
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT id, name, price, stock FROM drinks "
                "WHERE deleted_at IS NULL AND (barcode=? OR CAST(id AS TEXT)=?) LIMIT 1",
                (code, code),
            ).fetchone()
        if not row:
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, "drink": {
            "id": row["id"], "name": row["name"],
            "price": row["price"], "stock": row["stock"],
        }})

    @app.get("/api/discounts/count")
    @login_required
    def api_discount_count():
        active = sum(1 for d in discounts.list_discounts() if d.active)
        return jsonify({"count": active})

    @app.get("/qr")
    def qr_generic():
        """Beliebige URL/Pfad als QR-PNG rendern.

        Query-Parameter:
          * ``path=/kiosk``  → automatische Kombination mit public_base_url
          * ``url=...``      → wird 1:1 verwendet
        """
        raw = request.args.get("url")
        path = request.args.get("path")
        if raw:
            payload = raw
        elif path:
            if not path.startswith("/"):
                path = "/" + path
            payload = network.public_base_url() + path
        else:
            payload = network.public_base_url()
        return _qr_png(payload, download_name="kasse_qr.png")

    @app.route("/drinks/edit/<int:drink_id>", methods=["GET", "POST"])
    @login_required
    def drink_edit(drink_id: int):
        conn = database.get_connection()
        if request.method == "POST":
            name = request.form.get("name")
            price_euro = request.form.get("price", type=float)
            stock = request.form.get("stock", type=int)
            min_stock = request.form.get("min_stock", type=int)
            page = request.form.get("page", type=int) or 1
            cat = request.form.get("category_id", type=int)
            image_path = request.form.get("current_image") or None
            image_file = request.files.get("image")
            if image_file and image_file.filename:
                new_name = security.safe_upload_name(image_file.filename)
                if new_name:
                    image_dir = Path(__file__).resolve().parent.parent / "data" / "images"
                    image_dir.mkdir(parents=True, exist_ok=True)
                    dest = image_dir / new_name
                    image_file.save(dest)
                    image_path = str(dest)
            old = conn.execute("SELECT price FROM drinks WHERE id=?", (drink_id,)).fetchone()
            barcode = (request.form.get("barcode") or "").strip() or None
            conn.execute(
                "UPDATE drinks SET name=?, price=?, stock=?, min_stock=?, page=?, image=?, "
                "category_id=?, barcode=? WHERE id=?",
                (name, int(price_euro * 100), stock or 0, min_stock or 0, page, image_path,
                 cat, barcode, drink_id),
            )
            new_price = int(price_euro * 100)
            if old and old["price"] != new_price:
                conn.execute(
                    "INSERT INTO price_history (drink_id, old_price, new_price, changed_by) "
                    "VALUES (?, ?, ?, ?)",
                    (drink_id, old["price"], new_price, _current_user()),
                )
            conn.commit()
            conn.close()
            database.touch_refresh_flag()
            _audit("drink.edit", target=str(drink_id))
            return redirect(url_for("drinks"))
        item = conn.execute("SELECT * FROM drinks WHERE id=?", (drink_id,)).fetchone()
        categories = conn.execute(
            "SELECT id, name FROM drink_categories ORDER BY sort_order, name"
        ).fetchall()
        conn.close()
        return render_template("drink_edit.html", drink=item, categories=categories, error=None)

    # ------------------------------------------------------------------ Kategorien
    @app.route("/categories")
    @role_required("admin")
    def category_view():
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM drinks WHERE category_id=c.id AND deleted_at IS NULL) AS count "
                "FROM drink_categories c ORDER BY sort_order, name"
            ).fetchall()
        return render_template("categories.html", categories=rows)

    @app.route("/categories/add", methods=["POST"])
    @role_required("admin")
    def category_add():
        name = (request.form.get("name") or "").strip()
        color = request.form.get("color") or None
        sort_order = request.form.get("sort_order", type=int) or 0
        if name:
            try:
                with database.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO drink_categories (name, color, sort_order) VALUES (?, ?, ?)",
                        (name, color, sort_order),
                    )
                    conn.commit()
                _audit("category.add", target=name)
            except sqlite3.IntegrityError:
                pass
        return redirect(url_for("category_view"))

    @app.route("/categories/<int:cat_id>/delete", methods=["POST"])
    @role_required("admin")
    def category_delete(cat_id: int):
        with database.get_connection() as conn:
            conn.execute("UPDATE drinks SET category_id=NULL WHERE category_id=?", (cat_id,))
            conn.execute("DELETE FROM drink_categories WHERE id=?", (cat_id,))
            conn.commit()
        _audit("category.delete", target=str(cat_id))
        return redirect(url_for("category_view"))

    # ------------------------------------------------------------------ Discounts
    @app.route("/discounts")
    @login_required
    def discount_list():
        return render_template("discounts.html", discounts=discounts.list_discounts())

    @app.route("/discounts/add", methods=["POST"])
    @login_required
    def discount_add():
        name = (request.form.get("name") or "").strip()
        percent = request.form.get("percent", type=int) or 0
        start_time = request.form.get("start_time") or None
        end_time = request.form.get("end_time") or None
        weekdays_raw = request.form.get("weekdays") or ""
        weekdays = [int(x) for x in weekdays_raw.split(",") if x.strip().isdigit()]
        drink_ids_raw = request.form.get("drink_ids") or ""
        drink_ids = [int(x) for x in drink_ids_raw.split(",") if x.strip().isdigit()]
        if name and 0 < percent <= 100:
            new_id = discounts.add_discount(
                name, percent, start_time=start_time, end_time=end_time,
                weekdays=weekdays or None, drink_ids=drink_ids or None,
            )
            _audit("discount.add", target=str(new_id), details=f"{percent}%")
        return redirect(url_for("discount_list"))

    @app.route("/discounts/delete/<int:discount_id>", methods=["POST"])
    @login_required
    def discount_delete(discount_id: int):
        discounts.delete_discount(discount_id)
        _audit("discount.delete", target=str(discount_id))
        return redirect(url_for("discount_list"))

    @app.route("/discounts/toggle/<int:discount_id>", methods=["POST"])
    @login_required
    def discount_toggle(discount_id: int):
        active = request.form.get("active") == "1"
        discounts.set_active(discount_id, active)
        _audit("discount.toggle", target=str(discount_id))
        return redirect(url_for("discount_list"))

    # ------------------------------------------------------------------ Users
    @app.route("/users")
    @login_required
    def users(error: str | None = None):
        conn = database.get_connection()
        items = conn.execute(
            "SELECT * FROM users WHERE is_event=0 AND deleted_at IS NULL ORDER BY name"
        ).fetchall()
        conn.close()
        return render_template("users.html", users=items, error=error)

    @app.route("/event_cards")
    @login_required
    def event_cards(error: str | None = None):
        conn = database.get_connection()
        items = conn.execute(
            "SELECT * FROM users WHERE is_event=1 AND deleted_at IS NULL ORDER BY name"
        ).fetchall()
        conn.close()
        return render_template("event_cards.html", users=items, error=error)

    @app.route("/topup")
    @login_required
    def topup():
        conn = database.get_connection()
        items = conn.execute(
            "SELECT id, name FROM users WHERE is_event=0 AND deleted_at IS NULL ORDER BY name"
        ).fetchall()
        conn.close()
        return render_template("topup.html", users=items)

    @app.route("/topup/submit", methods=["POST"])
    @login_required
    def topup_submit():
        uid = request.form.get("uid")
        name = request.form.get("user_name")
        amount_euro = request.form.get("amount", type=float)
        if amount_euro is None:
            return redirect(url_for("topup"))
        user = None
        if uid:
            user = models.get_user_by_uid(uid)
        elif name:
            with database.get_connection() as conn:
                row = conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
            if row:
                user = models.User(**{k: row[k] for k in row.keys() if k != "deleted_at"})
        if user:
            cents = int(amount_euro * 100)
            models.update_balance(user.id, cents)
            models.add_topup(user.id, cents)
            ledger.on_cash_topup(cents, ref=f"topup user={user.id}",
                                 actor=_current_user())
            _audit("topup", target=f"user:{user.id}",
                   details=f"amount_cents={cents}")
            _act("topup", target=user.name, amount_cents=cents)
        return redirect(url_for("topup"))

    @app.route("/topup_log")
    @login_required
    def topup_log():
        page = request.args.get("page", default=1, type=int)
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM topups").fetchone()[0]
            pages = max((count + PER_PAGE - 1) // PER_PAGE, 1)
            offset = (page - 1) * PER_PAGE
            items = conn.execute(
                "SELECT t.id, t.timestamp, u.name as user_name, t.amount "
                "FROM topups t JOIN users u ON u.id = t.user_id "
                "ORDER BY t.timestamp DESC LIMIT ? OFFSET ?",
                (PER_PAGE, int(offset)),
            ).fetchall()
        return render_template("topup_log.html", items=items, page=page, pages=pages)

    @app.route("/topup_log/clear", methods=["POST"])
    @role_required("admin")
    def topup_log_clear():
        with database.get_connection() as conn:
            conn.execute("DELETE FROM topups")
            conn.commit()
        _audit("topup_log.clear")
        return redirect(url_for("topup_log"))

    @app.route("/topup_log/delete/<int:topup_id>", methods=["POST"])
    @role_required("admin")
    def topup_delete(topup_id: int):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM topups WHERE id=?", (topup_id,))
            conn.commit()
        _audit("topup.delete", target=str(topup_id))
        return redirect(url_for("topup_log"))

    @app.route("/users/add", methods=["POST"])
    @login_required
    def user_add():
        name = (request.form.get("name") or "").strip()
        uid = (request.form.get("uid") or "").strip() or None
        balance_euro = request.form.get("balance", type=float)
        error = None
        if name:
            with database.get_connection() as conn:
                try:
                    conn.execute(
                        "INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)",
                        (name, uid, int(balance_euro * 100) if balance_euro is not None else 0),
                    )
                    conn.commit()
                    database.touch_refresh_flag()
                    _audit("user.add", target=name)
                except sqlite3.IntegrityError:
                    error = "RFID-UID bereits vergeben"
        if error:
            return users(error=error)
        return redirect(url_for("users"))

    @app.route("/users/<int:user_id>")
    @login_required
    def user_details(user_id: int):
        with database.get_connection() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not u:
                abort(404)
            purchases = conn.execute(
                "SELECT t.timestamp, d.name AS drink, t.quantity, d.price "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "WHERE t.user_id=? ORDER BY t.id DESC LIMIT 100",
                (user_id,),
            ).fetchall()
            topups = conn.execute(
                "SELECT timestamp, amount FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 50",
                (user_id,),
            ).fetchall()
            month = conn.execute(
                "SELECT strftime('%Y-%m', t.timestamp) AS ym, SUM(t.quantity * d.price) AS spent "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "WHERE t.user_id=? GROUP BY ym ORDER BY ym",
                (user_id,),
            ).fetchall()
            total_spent = conn.execute(
                "SELECT COALESCE(SUM(t.quantity * d.price), 0) AS s "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id WHERE t.user_id=?",
                (user_id,),
            ).fetchone()["s"] or 0
            total_topup = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM topups WHERE user_id=?", (user_id,)
            ).fetchone()["s"] or 0
        return render_template(
            "user_details.html",
            user=u,
            purchases=purchases,
            topups=topups,
            monthly=[{"ym": r["ym"], "spent": int(r["spent"] or 0)} for r in month],
            total_spent=int(total_spent),
            total_topup=int(total_topup),
        )

    @app.route("/users/<int:user_id>/statement.pdf")
    @login_required
    def user_statement_pdf(user_id: int):
        with database.get_connection() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not u:
                abort(404)
            rows = conn.execute(
                "SELECT t.timestamp, d.name AS drink, t.quantity, d.price "
                "FROM transactions t JOIN drinks d ON d.id=t.drink_id "
                "WHERE t.user_id=? ORDER BY t.timestamp",
                (user_id,),
            ).fetchall()
            topups = conn.execute(
                "SELECT timestamp, amount FROM topups WHERE user_id=? ORDER BY timestamp",
                (user_id,),
            ).fetchall()
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14); pdf.cell(0, 8, f"Kontoauszug {u['name']}", ln=1)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 6, f"Aktuelles Guthaben: {(u['balance'] or 0)/100:+.2f} EUR", ln=1); pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 6, "Aufladungen", ln=1)
        pdf.set_font("Helvetica", size=9)
        for r in topups:
            pdf.cell(60, 5, r["timestamp"][:16]); pdf.cell(0, 5, f"+ {r['amount']/100:.2f} EUR", ln=1)
        pdf.ln(2); pdf.set_font("Helvetica", "B", 11); pdf.cell(0, 6, "Kauefe", ln=1)
        pdf.set_font("Helvetica", size=9)
        for r in rows:
            pdf.cell(50, 5, r["timestamp"][:16])
            pdf.cell(70, 5, f"{r['quantity']}x {r['drink']}")
            pdf.cell(0, 5, f"{r['quantity']*r['price']/100:.2f} EUR", ln=1)
        return send_file(io.BytesIO(pdf.output()), mimetype="application/pdf",
                         download_name=f"kontoauszug_{user_id}.pdf")

    @app.route("/users/inline", methods=["POST"])
    @login_required
    def user_inline():
        user_id = request.form.get("id", type=int)
        field = request.form.get("field")
        value = request.form.get("value")
        if not user_id or field not in {"balance", "name", "active"}:
            abort(400)
        with database.get_connection() as conn:
            if field == "balance":
                cents = int(round(float(value) * 100))
                conn.execute("UPDATE users SET balance=? WHERE id=?", (cents, user_id))
                shown = f"{cents/100:.2f} €"
            elif field == "active":
                v = 1 if value in ("1", "true", "on") else 0
                conn.execute("UPDATE users SET active=? WHERE id=?", (v, user_id))
                shown = "aktiv" if v else "gesperrt"
            else:
                conn.execute("UPDATE users SET name=? WHERE id=?", (value.strip(), user_id))
                shown = value.strip()
            conn.commit()
        _audit("user.inline", target=str(user_id), details=f"{field}={value}")
        resp = make_response(shown)
        resp.headers["HX-Trigger"] = json.dumps(
            {"showToast": {"msg": "Gespeichert", "level": "success", "duration": 1800}}
        )
        return resp

    @app.route("/users/bulk", methods=["POST"])
    @role_required("admin")
    def users_bulk():
        ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.strip().isdigit()]
        action = request.form.get("action")
        if ids and action in {"activate", "deactivate"}:
            v = 1 if action == "activate" else 0
            with database.get_connection() as conn:
                conn.executemany(
                    "UPDATE users SET active=? WHERE id=?", [(v, i) for i in ids]
                )
                conn.commit()
            _audit(f"user.bulk_{action}", details=str(ids))
        return redirect(url_for("users"))

    @app.route("/users/delete/<int:user_id>", methods=["POST"])
    @login_required
    def user_delete(user_id: int):
        with database.get_connection() as conn:
            row = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
            conn.execute("UPDATE users SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (user_id,))
            conn.commit()
        _audit("user.softdelete", target=str(user_id))
        session["undo"] = {"kind": "user", "id": user_id,
                           "label": row["name"] if row else "Nutzer"}
        return redirect(url_for("users"))

    @app.route("/users/qr/<int:user_id>")
    @login_required
    def user_qr(user_id: int):
        user = models.get_user(user_id)
        if not user or not user.rfid_uid:
            abort(404)
        token = make_token(user.rfid_uid)
        url = _absolute_url("selfservice.show", token=token)
        import qrcode
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png", download_name=f"user_{user_id}_qr.png")

    @app.route("/users/transfer/<int:user_id>", methods=["POST"])
    @role_required("admin")
    def user_transfer(user_id: int):
        """Kartenverlust: Guthaben von alter Karte auf neue UID übertragen."""
        new_uid = (request.form.get("new_uid") or "").strip()
        if not new_uid:
            return redirect(url_for("user_edit", user_id=user_id))
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT id, name, balance, rfid_uid FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not row:
                abort(404)
            # Alte Karte deaktivieren.
            conn.execute(
                "UPDATE users SET active=0, deleted_at=CURRENT_TIMESTAMP, rfid_uid=NULL WHERE id=?",
                (user_id,),
            )
            # Neue Karte mit Guthaben anlegen.
            conn.execute(
                "INSERT INTO users (name, rfid_uid, balance, active) VALUES (?, ?, ?, 1)",
                (row["name"], new_uid, row["balance"]),
            )
            conn.commit()
        _audit("user.transfer", target=str(user_id),
               details=f"{row['rfid_uid']} → {new_uid}")
        _act("user.transfer", target=row["name"], note=f"{row['rfid_uid']} → {new_uid}")
        return redirect(url_for("users"))

    @app.route("/event_cards/add", methods=["POST"])
    @login_required
    def event_card_add():
        name = request.form.get("name")
        uid = request.form.get("uid")
        show_on_payment = 1 if request.form.get("show_on_payment") else 0
        valid_from = (request.form.get("valid_from") or "").strip() or None
        valid_until = (request.form.get("valid_until") or "").strip() or None
        error = None
        if name and uid:
            with database.get_connection() as conn:
                try:
                    conn.execute(
                        "INSERT INTO users (name, rfid_uid, balance, is_event, active, "
                        "show_on_payment, valid_from, valid_until) "
                        "VALUES (?, ?, 0, 1, 1, ?, ?, ?)",
                        (name, uid, show_on_payment, valid_from, valid_until),
                    )
                    conn.commit()
                    _audit("event_card.add", target=name)
                except sqlite3.IntegrityError:
                    error = "RFID-UID bereits vergeben"
        if error:
            return event_cards(error=error)
        return redirect(url_for("event_cards"))

    @app.route("/users/topup", methods=["POST"])
    @login_required
    def users_topup():
        uid = request.form.get("uid")
        amount_euro = request.form.get("amount", type=float)
        if uid and amount_euro is not None:
            user = models.get_user_by_uid(uid)
            if user:
                cents = int(amount_euro * 100)
                models.update_balance(user.id, cents)
                models.add_topup(user.id, cents)
                ledger.on_cash_topup(cents, ref=f"topup user={user.id}",
                                     actor=_current_user())
                _audit("topup", target=f"user:{user.id}",
                       details=f"amount_cents={cents}")
                _act("topup", target=user.name, amount_cents=cents)
                return redirect(url_for("users"))
            return users(error="Unbekannte UID")
        return users(error="Ungültige Eingabe")

    @app.route("/event_cards/delete/<int:user_id>", methods=["POST"])
    @login_required
    def event_card_delete(user_id: int):
        with database.get_connection() as conn:
            conn.execute("UPDATE users SET deleted_at=CURRENT_TIMESTAMP WHERE id = ? AND is_event=1",
                         (user_id,))
            conn.commit()
        _audit("event_card.delete", target=str(user_id))
        return redirect(url_for("event_cards"))

    @app.route("/event_cards/reset/<int:user_id>", methods=["POST"])
    @login_required
    def event_card_reset(user_id: int):
        models.reset_event_card(user_id)
        _audit("event_card.reset", target=str(user_id))
        return redirect(url_for("event_cards"))

    @app.route("/event_cards/print/<int:user_id>")
    @login_required
    def event_card_print(user_id: int):
        conn = database.get_connection()
        user = conn.execute("SELECT * FROM users WHERE id=? AND is_event=1", (user_id,)).fetchone()
        if not user:
            conn.close()
            return redirect(url_for("event_cards"))
        query = ("SELECT t.timestamp, d.name, t.quantity, d.price "
                 "FROM transactions t JOIN drinks d ON d.id = t.drink_id "
                 "WHERE t.user_id=? ")
        params: list = [user_id]
        if user["active"] and (user["valid_from"] or user["valid_until"]):
            if user["valid_from"]:
                query += "AND DATE(t.timestamp) >= ? "
                params.append(user["valid_from"])
            if user["valid_until"]:
                query += "AND DATE(t.timestamp) <= ? "
                params.append(user["valid_until"])
        query += "ORDER BY t.timestamp"
        items = conn.execute(query, params).fetchall()
        conn.close()
        total = sum(r["quantity"] * r["price"] for r in items)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f'Firma: {user["name"]}', ln=1)
        if user["valid_from"] or user["valid_until"]:
            pdf.cell(0, 10, f'Gültig: {user["valid_from"] or ""} - {user["valid_until"] or ""}', ln=1)
        if user["created_at"]:
            pdf.cell(0, 10, f'Erstellt am: {user["created_at"][:10]}', ln=1)
        pdf.ln(4)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(40, 8, "Datum", 1); pdf.cell(70, 8, "Getränk", 1)
        pdf.cell(20, 8, "Anzahl", 1, align="R"); pdf.cell(20, 8, "Preis", 1, align="R")
        pdf.cell(20, 8, "Summe", 1, align="R"); pdf.ln()
        for r in items:
            pdf.cell(40, 8, r["timestamp"][:10], 1); pdf.cell(70, 8, r["name"], 1)
            pdf.cell(20, 8, str(r["quantity"]), 1, align="R")
            pdf.cell(20, 8, f"{r['price']/100:.2f}", 1, align="R")
            pdf.cell(20, 8, f"{r['quantity']*r['price']/100:.2f}", 1, align="R"); pdf.ln()
        pdf.cell(150, 8, "Gesamt", 1); pdf.cell(20, 8, f"{total/100:.2f}", 1, align="R")
        return send_file(io.BytesIO(pdf.output()), mimetype="application/pdf",
                         download_name=f"event_{user_id}.pdf")

    @app.route("/users/edit/<int:user_id>", methods=["GET", "POST"])
    @login_required
    def user_edit(user_id: int):
        conn = database.get_connection()
        if request.method == "POST":
            name = request.form.get("name")
            uid = request.form.get("uid")
            balance_euro = request.form.get("balance", type=float)
            is_event = 1 if request.form.get("is_event") else 0
            is_admin = 1 if request.form.get("is_admin") else 0
            is_buyer = 1 if request.form.get("is_buyer") else 0
            active = 1 if request.form.get("active") else 0
            show_on_payment = 1 if request.form.get("show_on_payment") and is_event else 0
            valid_from = (request.form.get("valid_from") or "").strip() or None
            valid_until = (request.form.get("valid_until") or "").strip() or None
            if not is_event:
                valid_from = None
                valid_until = None
            conn.execute(
                "UPDATE users SET name=?, rfid_uid=?, balance=?, is_event=?, active=?, "
                "show_on_payment=?, is_admin=?, is_buyer=?, valid_from=?, valid_until=? WHERE id=?",
                (name, uid, int(balance_euro * 100) if balance_euro is not None else 0,
                 is_event, active, show_on_payment, is_admin, is_buyer,
                 valid_from, valid_until, user_id),
            )
            conn.commit()
            conn.close()
            _audit("user.edit", target=str(user_id))
            return redirect(url_for("event_cards" if is_event else "users"))
        item = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        conn.close()
        return render_template("user_edit.html", user=item)

    # ------------------------------------------------------------------ Logs / Audit
    @app.route("/log")
    @login_required
    def log():
        tx_page = request.args.get("tx_page", default=1, type=int)
        restock_page = request.args.get("restock_page", default=1, type=int)
        with database.get_connection() as conn:
            tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            tx_pages = max((tx_count + PER_PAGE - 1) // PER_PAGE, 1)
            tx_offset = (tx_page - 1) * PER_PAGE
            items = conn.execute(
                "SELECT t.id, t.timestamp, u.name as user_name, d.name as drink_name, t.quantity "
                "FROM transactions t JOIN users u ON u.id = t.user_id "
                "JOIN drinks d ON d.id = t.drink_id "
                "ORDER BY t.timestamp DESC LIMIT ? OFFSET ?",
                (PER_PAGE, int(tx_offset)),
            ).fetchall()
            restock_count = conn.execute("SELECT COUNT(*) FROM restocks").fetchone()[0]
            restock_pages = max((restock_count + PER_PAGE - 1) // PER_PAGE, 1)
            restock_offset = (restock_page - 1) * PER_PAGE
            restocks = conn.execute(
                "SELECT r.id, r.timestamp, d.name as drink_name, r.quantity "
                "FROM restocks r JOIN drinks d ON d.id = r.drink_id "
                "ORDER BY r.timestamp DESC LIMIT ? OFFSET ?",
                (PER_PAGE, int(restock_offset)),
            ).fetchall()
        return render_template("log.html", items=items, restocks=restocks,
                               tx_page=tx_page, tx_pages=tx_pages,
                               restock_page=restock_page, restock_pages=restock_pages)

    @app.route("/log/transactions_clear", methods=["POST"])
    @role_required("admin")
    def transactions_clear():
        with database.get_connection() as conn:
            conn.execute("DELETE FROM transactions"); conn.commit()
        _audit("transactions.clear"); return redirect(url_for("log"))

    @app.route("/log/transaction_delete/<int:tx_id>", methods=["POST"])
    @role_required("admin")
    def transaction_delete(tx_id: int):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,)); conn.commit()
        _audit("transaction.delete", target=str(tx_id)); return redirect(url_for("log"))

    @app.route("/log/restocks_clear", methods=["POST"])
    @role_required("admin")
    def restocks_clear():
        with database.get_connection() as conn:
            conn.execute("DELETE FROM restocks"); conn.commit()
        _audit("restocks.clear"); return redirect(url_for("log"))

    @app.route("/log/restock_delete/<int:restock_id>", methods=["POST"])
    @role_required("admin")
    def restock_delete(restock_id: int):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM restocks WHERE id=?", (restock_id,)); conn.commit()
        _audit("restock.delete", target=str(restock_id)); return redirect(url_for("log"))

    @app.route("/audit")
    @login_required
    def audit_view():
        return render_template("audit.html", entries=audit.fetch(limit=500))

    # ------------------------------------------------------------------ Exports
    @app.route("/export/transactions")
    @login_required
    def export_transactions():
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT t.timestamp, u.name as user_name, d.name as drink_name, t.quantity "
                "FROM transactions t JOIN users u ON u.id = t.user_id "
                "JOIN drinks d ON d.id = t.drink_id ORDER BY t.timestamp DESC"
            ).fetchall()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["timestamp", "user", "drink", "quantity"])
        for r in rows:
            w.writerow([r["timestamp"], r["user_name"], r["drink_name"], r["quantity"]])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=transactions.csv"
        return resp

    @app.route("/export/transactions_anonymized")
    @login_required
    def export_transactions_anonymized():
        period = request.args.get("period", default="month", type=str)
        date_clause = _date_filter_clause(period)
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT t.timestamp, d.name as drink_name, t.quantity "
                "FROM transactions t JOIN drinks d ON d.id = t.drink_id "
                f"WHERE DATE(t.timestamp) >= {date_clause} ORDER BY t.timestamp DESC"
            ).fetchall()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["period", "timestamp", "drink", "quantity"])
        for r in rows:
            w.writerow([period, r["timestamp"], r["drink_name"], r["quantity"]])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=transactions_anonymized.csv"
        return resp

    @app.route("/export/inventory")
    @login_required
    def export_inventory():
        with database.get_connection() as conn:
            rows = conn.execute("SELECT name, price, stock FROM drinks WHERE deleted_at IS NULL ORDER BY name").fetchall()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["name", "price_euro", "stock"])
        for r in rows:
            w.writerow([r["name"], f"{r['price']/100:.2f}", r["stock"]])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=inventory.csv"
        return resp

    @app.route("/export/users")
    @login_required
    def export_users():
        with database.get_connection() as conn:
            rows = conn.execute("SELECT name, rfid_uid, balance FROM users WHERE deleted_at IS NULL ORDER BY name").fetchall()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["name", "uid", "balance_euro"])
        for r in rows:
            w.writerow([r["name"], r["rfid_uid"], f"{r['balance']/100:.2f}"])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=users.csv"
        return resp

    @app.route("/import/users", methods=["GET", "POST"])
    @role_required("admin")
    def import_users():
        if request.method == "POST":
            file = request.files.get("file")
            if file and file.filename:
                raw = file.read().decode("utf-8")
                try:
                    dialect = csv.Sniffer().sniff(raw.splitlines()[0], delimiters=",;")
                except csv.Error:
                    dialect = csv.get_dialect("excel")
                reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
                added = 0
                with database.get_connection() as conn:
                    for row in reader:
                        name = (row.get("name") or "").strip()
                        uid = (row.get("uid") or "").strip()
                        val = row.get("balance_euro") or row.get("balance") or "0"
                        val = val.replace(",", ".")
                        try:
                            balance = int(float(val) * 100)
                        except ValueError:
                            balance = 0
                        if not name or not uid:
                            continue
                        try:
                            conn.execute(
                                "INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)",
                                (name, uid, balance),
                            )
                            added += 1
                        except sqlite3.IntegrityError:
                            pass
                    conn.commit()
                _audit("users.import", details=f"added={added}")
            return redirect(url_for("users"))
        return render_template("import_users.html")

    @app.route("/export/restocks")
    @login_required
    def export_restocks():
        rows = models.get_restock_log()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["timestamp", "drink", "quantity"])
        for r in rows:
            w.writerow([r["timestamp"], r["drink_name"], r["quantity"]])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=restocks.csv"
        return resp

    @app.route("/export/topups")
    @login_required
    def export_topups():
        rows = models.get_topup_log()
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["timestamp", "user", "amount_euro"])
        for r in rows:
            w.writerow([r["timestamp"], r["user_name"], f"{r['amount']/100:.2f}"])
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=topups.csv"
        return resp

    # ------------------------------------------------------------------ File-Logs & Backups
    @app.route("/file_logs")
    @login_required
    def file_logs():
        page = request.args.get("page", default=1, type=int)
        log_dir = Path(__file__).resolve().parents[1] / "logs"
        files = sorted(log_dir.glob("log_*.txt"), reverse=True) + sorted(log_dir.glob("app.log*"), reverse=True) if log_dir.exists() else []
        total = len(files)
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        start = (page - 1) * PER_PAGE
        files = files[start:start + PER_PAGE]
        return render_template("file_logs.html", files=[f.name for f in files], page=page, pages=pages)

    @app.route("/file_logs/delete/<name>", methods=["POST"])
    @role_required("admin")
    def file_logs_delete(name: str):
        log_dir = Path(__file__).resolve().parents[1] / "logs"
        target = (log_dir / name).resolve()
        if not str(target).startswith(str(log_dir.resolve())):
            abort(400)
        if target.exists() and target.is_file():
            target.unlink()
            _audit("file_log.delete", target=name)
        return redirect(url_for("file_logs"))

    # ---------------- Kassenbuch ----------------
    def _receipt_relative_ok(path_str: str) -> Path | None:
        """Validiert einen relativen Beleg-Pfad und liefert absolute Datei."""
        base = database.DB_PATH.parent.resolve()
        try:
            target = (base / path_str).resolve()
        except (OSError, ValueError):
            return None
        if not str(target).startswith(str(base)):
            return None
        return target if target.exists() else None

    @app.route("/kassenbuch")
    @login_required
    def ledger_view():
        accs = ledger.list_accounts()
        rows = [{"account": a, "balance": ledger.balance_of(a.id)} for a in accs]
        recent = ledger.entries(limit=50)
        return render_template("ledger.html", accounts=rows, entries=recent,
                               kinds=ledger.LEDGER_KINDS)

    @app.route("/kassenbuch/konto/<int:account_id>")
    @login_required
    def ledger_account(account_id: int):
        acc = ledger.get_account(account_id)
        if not acc:
            abort(404)
        rows = ledger.entries(account_id=account_id, limit=500)
        balance = ledger.balance_of(account_id)
        return render_template("ledger_account.html", account=acc,
                               entries=rows, balance=balance,
                               kinds=ledger.LEDGER_KINDS)

    @app.route("/kassenbuch/buchen", methods=["GET", "POST"])
    @role_required("cashier")
    def ledger_post():
        accs = ledger.list_accounts()
        if request.method == "POST":
            kind = request.form.get("kind", "other")
            from_id = request.form.get("from_account_id", type=int) or None
            to_id = request.form.get("to_account_id", type=int) or None
            amount_eur = request.form.get("amount", type=float) or 0.0
            note = (request.form.get("note") or "").strip() or None
            ref = (request.form.get("ref") or "").strip() or None
            receipt_file = request.files.get("receipt")
            receipt_path = ledger.save_receipt(receipt_file) if receipt_file else None
            try:
                entry_id = ledger.post(
                    kind, int(round(amount_eur * 100)),
                    from_account=from_id, to_account=to_id,
                    ref=ref, actor=_current_user(), note=note,
                    receipt_path=receipt_path,
                )
                _act("ledger", target=ledger.LEDGER_KINDS.get(kind, kind),
                     amount_cents=int(round(amount_eur * 100)), note=note)
                _audit("ledger.post", target=str(entry_id), details=kind)
            except ValueError as e:
                return render_template("ledger_post.html", accounts=accs,
                                       kinds=ledger.LEDGER_KINDS, error=str(e))
            return redirect(url_for("ledger_view"))
        preset = request.args.get("preset", "")
        return render_template("ledger_post.html", accounts=accs,
                               kinds=ledger.LEDGER_KINDS, preset=preset, error=None)

    @app.route("/kassenbuch/leeren", methods=["GET", "POST"])
    @role_required("cashier")
    def ledger_drain():
        terminal = ledger.get_account("terminal_cash")
        main = ledger.get_account("main_cash")
        current_terminal = ledger.balance_of(terminal.id) if terminal else 0
        if request.method == "POST":
            amount_eur = request.form.get("amount", type=float)
            if amount_eur is None:
                amount_cents = current_terminal
            else:
                amount_cents = int(round(amount_eur * 100))
            note = (request.form.get("note") or "Kasse geleert").strip()
            if amount_cents > 0:
                ledger.transfer_terminal_to_main(amount_cents,
                                                 actor=_current_user(), note=note)
                _act("ledger.drain", target="Kassenleerung",
                     amount_cents=amount_cents, note=note)
                _audit("ledger.drain", details=f"{amount_cents}ct")
            return redirect(url_for("ledger_view"))
        return render_template("ledger_drain.html",
                               terminal_balance=current_terminal,
                               main_balance=ledger.balance_of(main.id) if main else 0)

    @app.route("/kassenbuch/beleg/<int:entry_id>")
    @login_required
    def ledger_receipt(entry_id: int):
        with database.get_connection() as conn:
            row = conn.execute(
                "SELECT receipt_path FROM ledger_entries WHERE id=?", (entry_id,)
            ).fetchone()
        if not row or not row["receipt_path"]:
            abort(404)
        target = _receipt_relative_ok(row["receipt_path"])
        if not target:
            abort(404)
        return send_file(target)

    @app.route("/kassenbuch/entry/<int:entry_id>/reverse", methods=["POST"])
    @role_required("admin")
    def ledger_reverse(entry_id: int):
        try:
            new_id = ledger.reverse(entry_id, actor=_current_user())
            _audit("ledger.reverse", target=str(entry_id), details=f"→{new_id}")
        except ValueError:
            pass
        return redirect(request.referrer or url_for("ledger_view"))

    @app.route("/kassenbuch/konten", methods=["GET", "POST"])
    @role_required("admin")
    def ledger_accounts():
        if request.method == "POST":
            code = (request.form.get("code") or "").strip()
            name = (request.form.get("name") or "").strip()
            kind = request.form.get("kind") or "cash"
            note = (request.form.get("note") or "").strip() or None
            if code and name:
                try:
                    ledger.create_account(code, name, kind, note)
                    _audit("ledger.account_add", target=code)
                except sqlite3.IntegrityError:
                    pass
            return redirect(url_for("ledger_accounts"))
        accs = ledger.list_accounts()
        return render_template("ledger_accounts.html",
                               accounts=accs,
                               balances={a.id: ledger.balance_of(a.id) for a in accs})

    @app.route("/kassenbuch/konten/<int:account_id>/delete", methods=["POST"])
    @role_required("admin")
    def ledger_account_delete(account_id: int):
        if ledger.delete_account(account_id):
            _audit("ledger.account_delete", target=str(account_id))
        return redirect(url_for("ledger_accounts"))

    # ---------------- Wiederkehrende Ausgaben ----------------
    @app.route("/kassenbuch/wiederkehrend", methods=["GET", "POST"])
    @role_required("admin")
    def recurring_view():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            amount_euro = request.form.get("amount", type=float) or 0.0
            interval = request.form.get("interval") or "monthly"
            from_id = request.form.get("from_account_id", type=int) or None
            to_id = request.form.get("to_account_id", type=int) or None
            next_due = (request.form.get("next_due") or "").strip() or None
            auto = bool(request.form.get("auto_post"))
            note = (request.form.get("note") or "").strip() or None
            if name and amount_euro > 0:
                try:
                    recurring.create(
                        name, int(round(amount_euro * 100)),
                        from_account_id=from_id, to_account_id=to_id,
                        interval=interval, next_due=next_due,
                        auto_post=auto, note=note,
                    )
                    _audit("recurring.add", target=name)
                except ValueError:
                    pass
            return redirect(url_for("recurring_view"))
        return render_template(
            "recurring.html",
            items=recurring.list_all(),
            accounts=ledger.list_accounts(),
            intervals=sorted(recurring.INTERVALS),
        )

    @app.route("/kassenbuch/wiederkehrend/<int:rec_id>/post", methods=["POST"])
    @role_required("cashier")
    def recurring_post(rec_id: int):
        entry_id = recurring.post_once(rec_id, actor=_current_user())
        if entry_id:
            _audit("recurring.post", target=str(rec_id), details=str(entry_id))
            _act("recurring.post", target=str(rec_id), note=f"→ ledger #{entry_id}")
        return redirect(url_for("recurring_view"))

    @app.route("/kassenbuch/wiederkehrend/<int:rec_id>/delete", methods=["POST"])
    @role_required("admin")
    def recurring_delete(rec_id: int):
        recurring.delete(rec_id)
        _audit("recurring.delete", target=str(rec_id))
        return redirect(url_for("recurring_view"))

    @app.route("/kassenbuch/wiederkehrend/<int:rec_id>/toggle", methods=["POST"])
    @role_required("admin")
    def recurring_toggle(rec_id: int):
        active = request.form.get("active") == "1"
        recurring.update(rec_id, active=1 if active else 0)
        _audit("recurring.toggle", target=str(rec_id))
        return redirect(url_for("recurring_view"))

    @app.route("/kassenbuch/export.csv")
    @role_required("cashier")
    def ledger_export_csv():
        since = request.args.get("since")
        until = request.args.get("until")
        with database.get_connection() as conn:
            q = (
                "SELECT l.id, l.timestamp, l.kind, af.name AS from_name, at.name AS to_name, "
                "l.amount_cents, l.ref, l.actor, l.note, l.receipt_path "
                "FROM ledger_entries l "
                "LEFT JOIN accounts af ON af.id=l.from_account_id "
                "LEFT JOIN accounts at ON at.id=l.to_account_id "
                "WHERE 1=1 "
            )
            params: list = []
            if since:
                q += "AND l.timestamp >= ? "
                params.append(since)
            if until:
                q += "AND l.timestamp <= ? "
                params.append(until)
            q += "ORDER BY l.id"
            rows = conn.execute(q, params).fetchall()
        # UTF-8 mit BOM, Semikolon-Trennung → Excel-DE kompatibel
        buf = io.StringIO()
        buf.write("﻿")
        w = csv.writer(buf, delimiter=";")
        w.writerow(["ID", "Zeitstempel", "Art", "Von", "Nach",
                    "Betrag (EUR)", "Referenz", "Erfasser", "Notiz", "Beleg vorhanden"])
        for r in rows:
            w.writerow([
                r["id"], r["timestamp"], r["kind"],
                r["from_name"] or "", r["to_name"] or "",
                f"{r['amount_cents']/100:.2f}".replace(".", ","),
                r["ref"] or "", r["actor"] or "", r["note"] or "",
                "ja" if r["receipt_path"] else "nein",
            ])
        resp = make_response(buf.getvalue())
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = 'attachment; filename="kassenbuch.csv"'
        return resp

    @app.route("/kassenbuch/salden.csv")
    @role_required("cashier")
    def ledger_balances_csv():
        buf = io.StringIO(); buf.write("﻿")
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Code", "Konto", "Art", "Saldo (EUR)"])
        for a in ledger.list_accounts():
            w.writerow([a.code, a.name, a.kind,
                        f"{ledger.balance_of(a.id)/100:.2f}".replace(".", ",")])
        resp = make_response(buf.getvalue())
        resp.headers["Content-Type"] = "text/csv; charset=utf-8"
        resp.headers["Content-Disposition"] = 'attachment; filename="salden.csv"'
        return resp

    @app.route("/kassenbuch/bericht.pdf")
    @role_required("cashier")
    def ledger_report():
        since = request.args.get("since")  # z. B. 2026-01-01
        until = request.args.get("until")
        accs = ledger.list_accounts()
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, "Kassenbuch-Bericht", ln=1)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 6, f"Zeitraum: {since or 'Beginn'} – {until or 'heute'}", ln=1)
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(90, 7, "Konto", 1)
        pdf.cell(30, 7, "Kind", 1)
        pdf.cell(30, 7, "Δ (EUR)", 1, align="R")
        pdf.cell(30, 7, "Saldo (EUR)", 1, align="R"); pdf.ln()
        pdf.set_font("Helvetica", size=10)
        for a in accs:
            delta = ledger.sum_between(a.id, since, until)
            bal = ledger.balance_of(a.id)
            pdf.cell(90, 7, f"{a.name} ({a.code})", 1)
            pdf.cell(30, 7, a.kind, 1)
            pdf.cell(30, 7, f"{delta/100:+.2f}", 1, align="R")
            pdf.cell(30, 7, f"{bal/100:+.2f}", 1, align="R")
            pdf.ln()
        return send_file(io.BytesIO(pdf.output()), mimetype="application/pdf",
                         download_name="kassenbuch.pdf")

    @app.route("/backups")
    @login_required
    def backup_view():
        directory = Path(os.environ.get("GK_BACKUP_DIR") or (database.DB_PATH.parent / "backups"))
        entries = []
        if directory.exists():
            for f in sorted(directory.glob("gkasse_*.db.gz"), reverse=True):
                sha_file = f.with_suffix(f.suffix + ".sha256")
                sha = sha_file.read_text().split()[0] if sha_file.exists() else ""
                entries.append({"name": f.name, "size": f.stat().st_size,
                                "sha256": sha, "mtime": f.stat().st_mtime})
        usb_path = models.get_usb_backup_path()
        usb_mounted = bool(usb_path) and Path(usb_path).exists() and Path(usb_path).is_dir()
        return render_template("backups.html", backups=entries, directory=str(directory),
                               scheduler_status=scheduler.status(),
                               backup_hour=int(os.environ.get("GK_BACKUP_HOUR", "3")),
                               log_retention=int(os.environ.get("GK_LOG_RETENTION_DAYS", "30")),
                               usb_path=usb_path, usb_mounted=usb_mounted)

    @app.route("/backups/usb", methods=["POST"])
    @role_required("admin")
    def backup_usb_save():
        path = (request.form.get("usb_path") or "").strip()
        models.set_usb_backup_path(path)
        _audit("backup.usb_set", target=path or "(off)")
        return redirect(url_for("backup_view"))

    @app.route("/backups/create", methods=["POST"])
    @role_required("admin")
    def backup_create():
        info = backups.create_backup()
        _audit("backup.create", target=info.path.name, details=info.sha256)
        if os.environ.get("GK_BACKUP_WEBDAV_URL"):
            backups.upload_webdav(info)
        return redirect(url_for("backup_view"))

    @app.route("/backups/restore", methods=["POST"])
    @role_required("superadmin")
    def backup_restore():
        """Restore aus vorhandener Backup-Datei oder Upload."""
        directory = Path(os.environ.get("GK_BACKUP_DIR") or (database.DB_PATH.parent / "backups"))
        source_path: Path | None = None
        upload = request.files.get("upload")
        pick = (request.form.get("pick") or "").strip()
        if upload and upload.filename:
            tmp_dir = database.DB_PATH.parent / "restore_uploads"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            # Nur .db oder .db.gz akzeptieren, sicherer Name.
            from werkzeug.utils import secure_filename
            name = secure_filename(upload.filename)
            if not name.endswith(".db") and not name.endswith(".db.gz"):
                return "Nur .db oder .db.gz erlaubt", 400
            source_path = tmp_dir / name
            upload.save(source_path)
        elif pick:
            candidate = (directory / pick).resolve()
            if str(candidate).startswith(str(directory.resolve())) and candidate.exists():
                source_path = candidate
        if not source_path:
            return "Keine Backup-Datei ausgewählt", 400
        # Sicherung des aktuellen Standes VOR dem Restore.
        try:
            pre = backups.create_backup()
            _audit("backup.pre_restore", target=pre.path.name)
        except FileNotFoundError:
            pre = None
        try:
            backups.restore_backup(source_path)
            _audit("backup.restore", target=str(source_path.name),
                   details=f"pre_backup={pre.path.name if pre else '-'}")
        except Exception as e:
            _audit("backup.restore_failed", target=str(source_path.name), details=str(e))
            return f"Restore fehlgeschlagen: {e}", 500
        # Migrationen erneut anwenden, falls das Backup älteres Schema hatte.
        try:
            migrations.upgrade()
        except Exception as e:
            _audit("backup.restore_migrate_failed", details=str(e))
            return f"Migration nach Restore fehlgeschlagen: {e}", 500
        return render_template(
            "backup_restore_done.html",
            source=source_path.name,
            pre_backup=pre.path.name if pre else None,
        )

    @app.route("/backups/download/<name>")
    @role_required("admin")
    def backup_download(name: str):
        directory = Path(os.environ.get("GK_BACKUP_DIR") or (database.DB_PATH.parent / "backups"))
        target = (directory / name).resolve()
        if not str(target).startswith(str(directory.resolve())) or not target.exists():
            abort(404)
        return send_from_directory(directory, target.name, as_attachment=True)

    # ------------------------------------------------------------------ Kassensturz
    def _closing_account(code_or_id) -> ledger.Account | None:
        return ledger.get_account(code_or_id) if code_or_id else ledger.get_account("terminal_cash")

    @app.route("/kassensturz")
    @role_required("cashier")
    def kassensturz_view():
        chosen = _closing_account(request.args.get("account") or "terminal_cash")
        accounts = [a for a in ledger.list_accounts() if a.kind in ("cash", "bank")]
        target = ledger.balance_of(chosen.id) if chosen else 0
        with database.get_connection() as conn:
            history = conn.execute(
                "SELECT c.*, a.name AS account_name FROM cash_closings c "
                "LEFT JOIN accounts a ON a.id=c.account_id "
                "ORDER BY c.id DESC LIMIT 100"
            ).fetchall()
            last = conn.execute(
                "SELECT MAX(timestamp) FROM cash_closings WHERE account_id=?",
                (chosen.id if chosen else None,),
            ).fetchone()[0] if chosen else None
        return render_template("kassensturz.html", target=target, history=history,
                               since=last, accounts=accounts, chosen=chosen)

    @app.route("/kassensturz/submit", methods=["POST"])
    @role_required("cashier")
    def kassensturz_submit():
        actual = int((request.form.get("actual", type=float) or 0) * 100)
        cashier = (request.form.get("cashier") or _current_user() or "").strip()
        note = (request.form.get("note") or "").strip() or None
        acc_id = request.form.get("account_id", type=int)
        acc = ledger.get_account(acc_id) if acc_id else ledger.get_account("terminal_cash")
        if not acc:
            abort(400)
        target = ledger.balance_of(acc.id)
        diff = actual - target
        # 1) Buchhalterische Anpassung: Differenz auf/ab
        ledger_entry_id = None
        if diff != 0:
            if diff > 0:
                # zu viel Bargeld → Zugang aus 'sonstige Einnahmen'
                ledger_entry_id = ledger.post(
                    "closing", abs(diff),
                    from_account="income_other", to_account=acc.code,
                    actor=cashier or _current_user(),
                    note=f"Kassensturz-Ausgleich: +{diff/100:.2f} € auf {acc.name}",
                )
            else:
                ledger_entry_id = ledger.post(
                    "closing", abs(diff),
                    from_account=acc.code, to_account="expenses_other",
                    actor=cashier or _current_user(),
                    note=f"Kassensturz-Ausgleich: {diff/100:+.2f} € von {acc.name}",
                )
        with database.get_connection() as conn:
            conn.execute(
                "INSERT INTO cash_closings (cashier, target_cents, actual_cents, diff_cents, note, "
                " account_id, ledger_entry_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cashier, target, actual, diff, note, acc.id, ledger_entry_id),
            )
            conn.commit()
        _audit("kassensturz.submit", target=cashier, details=f"acc={acc.code} diff_cents={diff}")
        _act("kassensturz", target=cashier, amount_cents=diff, note=f"{acc.name}: {note or ''}")
        return redirect(url_for("kassensturz_view", account=acc.code))

    @app.route("/kassensturz/<int:closing_id>/pdf")
    @role_required("cashier")
    def kassensturz_pdf(closing_id: int):
        with database.get_connection() as conn:
            r = conn.execute("SELECT * FROM cash_closings WHERE id=?", (closing_id,)).fetchone()
        if not r:
            abort(404)
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Kassensturz", ln=1)
        pdf.set_font("Helvetica", size=11)
        pdf.cell(0, 8, f"Datum: {r['timestamp']}", ln=1)
        pdf.cell(0, 8, f"Kassierer: {r['cashier'] or '-'}", ln=1)
        pdf.ln(4)
        pdf.cell(60, 8, "Soll (Barverkäufe):", 0)
        pdf.cell(0, 8, f"{(r['target_cents'] or 0)/100:.2f} EUR", ln=1)
        pdf.cell(60, 8, "Ist:", 0)
        pdf.cell(0, 8, f"{(r['actual_cents'] or 0)/100:.2f} EUR", ln=1)
        pdf.cell(60, 8, "Differenz:", 0)
        pdf.cell(0, 8, f"{(r['diff_cents'] or 0)/100:+.2f} EUR", ln=1)
        pdf.ln(4)
        pdf.multi_cell(0, 8, f"Notiz: {r['note'] or '-'}")
        return send_file(io.BytesIO(pdf.output()), mimetype="application/pdf",
                         download_name=f"kassensturz_{closing_id}.pdf")

    # ------------------------------------------------------------------ Inventur
    @app.route("/inventur")
    @role_required("restocker")
    def inventur_view():
        with database.get_connection() as conn:
            drinks_ = conn.execute(
                "SELECT id, name, stock FROM drinks WHERE deleted_at IS NULL ORDER BY name"
            ).fetchall()
        return render_template("inventur.html", drinks=drinks_)

    @app.route("/inventur/submit", methods=["POST"])
    @role_required("restocker")
    def inventur_submit():
        deltas = []
        with database.get_connection() as conn:
            drinks_ = conn.execute(
                "SELECT id, name, stock FROM drinks WHERE deleted_at IS NULL"
            ).fetchall()
            for d in drinks_:
                key = f"ist_{d['id']}"
                if key not in request.form:
                    continue
                ist = int(request.form.get(key) or d["stock"])
                delta = ist - d["stock"]
                if delta != 0:
                    conn.execute("UPDATE drinks SET stock=? WHERE id=?", (ist, d["id"]))
                    conn.execute(
                        "INSERT INTO restocks (drink_id, quantity) VALUES (?, ?)",
                        (d["id"], delta),
                    )
                    deltas.append((d["name"], delta))
            conn.commit()
        _audit("inventur", details=f"{len(deltas)} Änderungen")
        for name, delta in deltas:
            _act("inventur", target=name, note=f"{delta:+d}")
        database.touch_refresh_flag()
        return redirect(url_for("inventur_view"))

    # ------------------------------------------------------------------ Wishes
    @app.route("/wishes")
    @login_required
    def wish_view():
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM drink_wishes ORDER BY votes DESC, name"
            ).fetchall()
        return render_template("wishes.html", wishes=rows)

    @app.route("/wishes/add", methods=["POST"])
    @login_required
    def wish_add():
        name = (request.form.get("name") or "").strip()
        if name:
            with database.get_connection() as conn:
                conn.execute("INSERT INTO drink_wishes (name, votes) VALUES (?, 0)", (name,))
                conn.commit()
            _audit("wish.add", target=name)
        return redirect(url_for("wish_view"))

    @app.route("/wishes/delete/<int:wish_id>", methods=["POST"])
    @login_required
    def wish_delete(wish_id: int):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM drink_wishes WHERE id=?", (wish_id,))
            conn.execute("DELETE FROM wish_votes WHERE wish_id=?", (wish_id,))
            conn.commit()
        _audit("wish.delete", target=str(wish_id))
        return redirect(url_for("wish_view"))

    @app.get("/api/wishes")
    @csrf.exempt
    def wish_list_api():
        with database.get_connection() as conn:
            try:
                rows = conn.execute(
                    "SELECT id, name, votes FROM drink_wishes ORDER BY votes DESC, name LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        return jsonify([{"id": r["id"], "name": r["name"], "votes": r["votes"]} for r in rows])

    @app.post("/api/wishes/propose")
    @csrf.exempt
    def wish_propose():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 60:
            return jsonify({"ok": False, "error": "invalid"}), 400
        with database.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO drink_wishes (name, votes) VALUES (?, 0)", (name,)
            )
            conn.commit()
        _audit("wish.propose", target=name)
        return jsonify({"ok": True, "id": int(cur.lastrowid)})

    @app.post("/api/wishes/vote")
    @csrf.exempt
    def wish_vote():
        data = request.get_json(silent=True) or {}
        uid = str(data.get("uid") or "").strip()
        wish_id = int(data.get("wish_id") or 0)
        if not uid or not wish_id:
            return jsonify({"ok": False, "error": "bad_request"}), 400
        week = datetime.utcnow().strftime("%Y-W%U")
        try:
            with database.get_connection() as conn:
                conn.execute(
                    "INSERT INTO wish_votes (wish_id, voter_uid, week) VALUES (?, ?, ?)",
                    (wish_id, uid, week),
                )
                conn.execute(
                    "UPDATE drink_wishes SET votes=votes+1 WHERE id=?", (wish_id,)
                )
                conn.commit()
            return jsonify({"ok": True})
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "already_voted"}), 409

    # ------------------------------------------------------------------ Webhooks
    @app.route("/webhooks")
    @role_required("admin")
    def webhook_view():
        return render_template("webhooks.html", hooks=webhooks.list_webhooks())

    @app.route("/webhooks/add", methods=["POST"])
    @role_required("admin")
    def webhook_add():
        name = request.form.get("name") or ""
        url = request.form.get("url") or ""
        events = request.form.getlist("events") or ["low_stock"]
        webhooks.add_webhook(name, url, events)
        _audit("webhook.add", target=name)
        return redirect(url_for("webhook_view"))

    @app.route("/webhooks/<int:hook_id>/delete", methods=["POST"])
    @role_required("admin")
    def webhook_delete(hook_id: int):
        webhooks.delete_webhook(hook_id)
        _audit("webhook.delete", target=str(hook_id))
        return redirect(url_for("webhook_view"))

    @app.route("/webhooks/<int:hook_id>/toggle", methods=["POST"])
    @role_required("admin")
    def webhook_toggle(hook_id: int):
        webhooks.toggle_webhook(hook_id, request.form.get("active") == "1")
        _audit("webhook.toggle", target=str(hook_id))
        return redirect(url_for("webhook_view"))

    # ------------------------------------------------------------------ System-Update
    _LAST_UPDATE = {"log": ""}

    def _git(*args) -> tuple[int, str]:
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True,
                cwd=Path(__file__).resolve().parents[2], timeout=60,
            )
            return out.returncode, (out.stdout + out.stderr).strip()
        except Exception as e:
            return 1, str(e)

    @app.route("/system/update")
    @role_required("superadmin")
    def system_update_view():
        _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        _, head = _git("rev-parse", "--short", "HEAD")
        _, log_out = _git("log", "--pretty=format:%h %s", "-n", "10")
        _, remote = _git("ls-remote", "--heads", "origin", branch or "main")
        behind = 0
        if remote and head and not remote.startswith(head[:7]):
            behind = 1
        git_info = {"branch": branch, "head": head, "log": log_out.splitlines() if log_out else [], "behind": behind}
        return render_template("system_update.html",
                               migrations=migrations.status(),
                               git=git_info,
                               last_run=_LAST_UPDATE["log"])

    @app.route("/system/update/run", methods=["POST"])
    @role_required("superadmin")
    def system_update_run():
        steps = []
        # 1) Backup
        try:
            info = backups.create_backup()
            steps.append(f"[OK] Backup {info.path.name} ({info.size} B)")
        except Exception as e:
            steps.append(f"[!!] Backup fehlgeschlagen: {e}")
            _LAST_UPDATE["log"] = "\n".join(steps)
            return redirect(url_for("system_update_view"))

        # 2) git pull
        rc, out = _git("pull", "--ff-only")
        steps.append(f"[{'OK' if rc == 0 else '!!'}] git pull\n{out}")
        if rc != 0:
            steps.append("Rollback: (kein Code-Rollback nötig, Backup vorhanden)")
            _LAST_UPDATE["log"] = "\n".join(steps)
            return redirect(url_for("system_update_view"))

        # 3) pip install
        try:
            venv_py = Path(os.environ.get("VIRTUAL_ENV", ".venv")) / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            py = str(venv_py) if venv_py.exists() else "python"
            out = subprocess.run(
                [py, "-m", "pip", "install", "-r", "requirements.txt"],
                capture_output=True, text=True, timeout=600,
                cwd=Path(__file__).resolve().parents[2],
            )
            steps.append(f"[{'OK' if out.returncode == 0 else '!!'}] pip install\n{out.stdout[-1000:]}\n{out.stderr[-500:]}")
        except Exception as e:
            steps.append(f"[!!] pip install: {e}")

        # 4) migrations
        try:
            applied = migrations.upgrade()
            steps.append(f"[OK] Migrations angewendet: {applied or 'keine'}")
        except Exception as e:
            steps.append(f"[!!] Migrationsfehler: {e} (Backup wurde restauriert)")

        _LAST_UPDATE["log"] = "\n".join(steps)
        _audit("system.update")
        return redirect(url_for("system_update_view"))

    # ------------------------------------------------------------------ API-Tokens
    @app.route("/api_tokens", methods=["GET", "POST"])
    @role_required("admin")
    def api_token_view():
        new_token = None
        if request.method == "POST":
            name = (request.form.get("name") or "unbenannt").strip()
            scopes = request.form.get("scopes") or "read"
            new_token, rec = issue_token(name, scopes)
            _audit("api_token.issue", target=str(rec.id), details=scopes)
        return render_template("api_tokens.html", tokens=list_tokens(), new_token=new_token)

    @app.route("/api_tokens/delete/<int:token_id>", methods=["POST"])
    @role_required("admin")
    def api_token_delete(token_id: int):
        revoke(token_id)
        _audit("api_token.revoke", target=str(token_id))
        return redirect(url_for("api_token_view"))

    # ------------------------------------------------------------------ Blueprints (API+SS)
    register_api(app)
    app.register_blueprint(selfservice_bp)
    csrf.exempt(app.blueprints["api_v1"])
    csrf.exempt(app.blueprints["selfservice"])

    notifier.start()
    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cli() -> None:  # pragma: no cover
    load_dotenv(override=False)
    host = os.environ.get("GK_HOST", "0.0.0.0")
    port = int(os.environ.get("GK_PORT", "8000"))
    app = create_app()
    if os.environ.get("GK_DEV") == "1":
        _LOG.info("dev server on %s:%s", host, port)
        app.run(host=host, port=port, debug=False)
        return
    from waitress import serve
    _LOG.info("waitress on %s:%s", host, port)
    serve(app, host=host, port=port, threads=8)


def main() -> None:
    cli()


if __name__ == "__main__":
    cli()
