from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Optional
import sqlite3

from .. import admin_auth

from flask import Flask, redirect, render_template, request, session, url_for, send_file
from flask import jsonify, make_response
import csv
import io


from .. import database, models


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = 'change-me'
    PER_PAGE = 25

    def login_required(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get('user'):
                return redirect(url_for('login'))
            return func(*args, **kwargs)
        return wrapper


    @app.route('/read_uid')
    @login_required
    def read_uid():
        uid = models.rfid_read_for_web()
        if uid is None:
            return jsonify({'uid': '', 'error': 'RFID-Reader nicht verfügbar'})
        return jsonify({'uid': uid})

    @app.route('/user_name')
    @login_required
    def user_name():
        uid = request.args.get('uid')
        name = ''
        if uid:
            user = models.get_user_by_uid(uid)
            if user:
                name = user.name
        return jsonify({'name': name})


    @app.route('/', methods=['GET'])
    def index():
        if not session.get('user'):
            return redirect(url_for('login'))
        conn = database.get_connection()
        to_buy = models.get_drinks_below_min(conn)
        row = conn.execute(
            'SELECT COALESCE(SUM(balance), 0) AS total FROM users WHERE is_event=0'
        ).fetchone()
        total_balance = row['total'] if row else 0
        conn.close()
        return render_template('index.html', to_buy=to_buy, total_balance=total_balance)


    @app.route('/dashboard')
    @login_required
    def dashboard():
        stats, totals = models.get_monthly_stats()
        return render_template('dashboard.html', stats=stats, totals=totals)

    @app.route('/dashboard/receipt')
    @login_required
    def dashboard_receipt():
        stats, totals = models.get_monthly_stats()
        return render_template('dashboard_receipt.html', stats=stats, totals=totals)


    @app.route('/refresh', methods=['POST'])
    @login_required
    def refresh():
        database.touch_refresh_flag()
        return redirect(url_for('index'))

    @app.route('/stop', methods=['POST'])
    @login_required
    def stop():
        database.set_exit_flag()
        func = request.environ.get('werkzeug.server.shutdown')
        if func:
            func()
        return 'Beende Anwendung...'

    @app.route('/password', methods=['GET', 'POST'])
    @login_required
    def change_password():
        error: Optional[str] = None
        info: Optional[str] = None
        if request.method == 'POST':
            old_pw = request.form.get('old_pw') or ''
            new_pw1 = request.form.get('new_pw1') or ''
            new_pw2 = request.form.get('new_pw2') or ''
            if not admin_auth.verify_password(old_pw):
                error = 'Altes Passwort falsch'
            elif not new_pw1:
                error = 'Neues Passwort darf nicht leer sein'
            elif new_pw1 != new_pw2:
                error = 'Passwörter stimmen nicht überein'
            else:
                admin_auth.set_password(new_pw1)
                info = 'Passwort gespeichert'
        return render_template('change_password.html', error=error, info=info)

    @app.route('/settings', methods=['GET', 'POST'])
    @login_required
    def settings():
        conn = database.get_connection()
        current_limit = models.get_overdraft_limit(conn)
        current_pin = models.get_admin_pin(conn)
        data_dir = Path(__file__).resolve().parent.parent / 'data'
        qr_path = data_dir / 'web_qr.png'
        bg_path = data_dir / 'background.png'
        thank_path = data_dir / 'background_thanks.png'
        if request.method == 'POST':
            val = request.form.get('overdraft', type=float)
            if val is not None:
                models.set_overdraft_limit(int(val * 100), conn)
            pin_val = request.form.get('admin_pin')
            if pin_val is not None:
                models.set_admin_pin(pin_val, conn)
            qr_file = request.files.get('qr_code')
            if qr_file and qr_file.filename:
                data_dir.mkdir(parents=True, exist_ok=True)
                qr_file.save(qr_path)
            bg_file = request.files.get('background')
            if bg_file and bg_file.filename:
                data_dir.mkdir(parents=True, exist_ok=True)
                bg_file.save(bg_path)
            thank_file = request.files.get('thank_background')
            if thank_file and thank_file.filename:
                data_dir.mkdir(parents=True, exist_ok=True)
                thank_file.save(thank_path)
            conn.close()
            return redirect(url_for('settings'))
        conn.close()
        return render_template('settings.html', overdraft_limit=current_limit,
                               admin_pin=current_pin,
                               qr_code_exists=qr_path.exists(),
                               background_exists=bg_path.exists(),
                               thank_background_exists=thank_path.exists())

    @app.route('/web_qr.png')
    @login_required
    def web_qr_png():
        path = Path(__file__).resolve().parent.parent / 'data' / 'web_qr.png'
        if path.exists():
            return send_file(path)
        return ('', 404)

    @app.route('/background.png')
    @login_required
    def background_png():
        path = Path(__file__).resolve().parent.parent / 'data' / 'background.png'
        if path.exists():
            return send_file(path)
        return ('', 404)

    @app.route('/thank_background.png')
    @login_required
    def thank_background_png():
        path = Path(__file__).resolve().parent.parent / 'data' / 'background_thanks.png'
        if path.exists():
            return send_file(path)
        return ('', 404)


    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error: Optional[str] = None
        if request.method == 'POST':
            user = request.form.get('username')
            pw = request.form.get('password')
            if user == 'admin' and admin_auth.verify_password(pw or ''):
                session['user'] = user
                return redirect(url_for('index'))
            error = 'Falsche Zugangsdaten'
        return render_template('login.html', error=error)

    @app.route('/logout')
    def logout():
        session.pop('user', None)
        return redirect(url_for('login'))

    @app.route('/drinks')
    @login_required
    def drinks(error: Optional[str] = None):
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM drinks ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('drinks.html', drinks=items, error=error)

    @app.route('/drinks/add', methods=['POST'])
    @login_required
    def drink_add():
        name = request.form.get('name')
        price_euro = request.form.get('price', type=float)
        stock = request.form.get('stock', type=int)
        min_stock = request.form.get('min_stock', type=int)
        page = request.form.get('page', type=int) or 1
        image_file = request.files.get('image')
        image_path = None
        if image_file and image_file.filename:
            image_dir = Path(__file__).resolve().parent.parent / 'data' / 'images'
            image_dir.mkdir(parents=True, exist_ok=True)
            dest = image_dir / image_file.filename
            image_file.save(dest)
            image_path = str(dest)

        if name and price_euro is not None:
            price = int(price_euro * 100)
            conn = database.get_connection()
            count = conn.execute('SELECT COUNT(*) FROM drinks WHERE page=?', (page,)).fetchone()[0]
            if count >= 9:
                cur = conn.execute('SELECT * FROM drinks ORDER BY name')
                items = cur.fetchall()
                conn.close()
                return render_template('drinks.html', drinks=items,
                                       error='Maximal 9 Getränke pro Seite erlaubt')
            conn.execute(
                'INSERT INTO drinks (name, price, stock, min_stock, page, image) VALUES (?, ?, ?, ?, ?, ?)',
                (name, price, stock or 0, min_stock or 0, page, image_path))
            conn.commit()
            conn.close()
            database.touch_refresh_flag()
        return redirect(url_for('drinks'))

    @app.route('/drinks/delete/<int:drink_id>')
    @login_required
    def drink_delete(drink_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM drinks WHERE id = ?', (drink_id,))
        conn.commit()
        conn.close()
        database.touch_refresh_flag()
        return redirect(url_for('drinks'))

    @app.route('/drinks/restock/<int:drink_id>', methods=['POST'])
    @login_required
    def drink_restock(drink_id: int):
        amount = request.form.get('amount', type=int)
        if amount and amount > 0:
            conn = database.get_connection()
            cur = conn.execute('SELECT stock FROM drinks WHERE id=?', (drink_id,))
            row = cur.fetchone()
            if row:
                new_stock = row['stock'] + amount
                conn.execute('UPDATE drinks SET stock=? WHERE id=?', (new_stock, drink_id))
                conn.commit()
                conn.close()
                models.log_restock(drink_id, amount)
                database.touch_refresh_flag()
            else:
                conn.close()
        return redirect(url_for('drinks'))

    @app.route('/drinks/edit/<int:drink_id>', methods=['GET', 'POST'])
    @login_required
    def drink_edit(drink_id: int):
        conn = database.get_connection()
        if request.method == 'POST':
            name = request.form.get('name')
            price_euro = request.form.get('price', type=float)
            stock = request.form.get('stock', type=int)
            min_stock = request.form.get('min_stock', type=int)
            page = request.form.get('page', type=int) or 1
            image_path = request.form.get('current_image') or None
            image_file = request.files.get('image')
            if image_file and image_file.filename:
                image_dir = Path(__file__).resolve().parent.parent / 'data' / 'images'
                image_dir.mkdir(parents=True, exist_ok=True)
                dest = image_dir / image_file.filename
                image_file.save(dest)
                image_path = str(dest)
            cur = conn.execute('SELECT page FROM drinks WHERE id=?', (drink_id,))
            old_page = cur.fetchone()['page']
            count = conn.execute('SELECT COUNT(*) FROM drinks WHERE page=?', (page,)).fetchone()[0]
            if count >= 9 and page != old_page:
                cur = conn.execute('SELECT * FROM drinks WHERE id=?', (drink_id,))
                item = cur.fetchone()
                conn.close()
                return render_template('drink_edit.html', drink=item,
                                       error='Maximal 9 Getränke pro Seite erlaubt')
            conn.execute(
                'UPDATE drinks SET name=?, price=?, stock=?, min_stock=?, page=?, image=? WHERE id=?',
                (name, int(price_euro * 100), stock or 0, min_stock or 0, page, image_path, drink_id))
            conn.commit()
            conn.close()
            database.touch_refresh_flag()
            return redirect(url_for('drinks'))
        cur = conn.execute('SELECT * FROM drinks WHERE id=?', (drink_id,))
        item = cur.fetchone()
        conn.close()
        return render_template('drink_edit.html', drink=item, error=None)

    @app.route('/users')
    @login_required
    def users(error: Optional[str] = None):
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM users WHERE is_event=0 ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('users.html', users=items, error=error)

    @app.route('/event_cards')
    @login_required
    def event_cards(error: Optional[str] = None):
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM users WHERE is_event=1 ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('event_cards.html', users=items, error=error)

    @app.route('/topup')
    @login_required
    def topup():
        conn = database.get_connection()
        cur = conn.execute('SELECT id, name FROM users WHERE is_event=0 ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('topup.html', users=items)

    @app.route('/topup/submit', methods=['POST'])
    @login_required
    def topup_submit():
        uid = request.form.get('uid')
        name = request.form.get('user_name')
        amount_euro = request.form.get('amount', type=float)
        if amount_euro is None:
            return redirect(url_for('topup'))
        user = None
        if uid:
            user = models.get_user_by_uid(uid)
        elif name:
            conn = database.get_connection()
            cur = conn.execute('SELECT * FROM users WHERE name=?', (name,))
            row = cur.fetchone()
            conn.close()
            if row:
                user = models.User(**row)
        if user:
            cents = int(amount_euro * 100)
            models.update_balance(user.id, cents)
            models.add_topup(user.id, cents)
        return redirect(url_for('topup'))

    @app.route('/topup_log')
    @login_required
    def topup_log():
        page = request.args.get('page', default=1, type=int)
        conn = database.get_connection()
        count = conn.execute('SELECT COUNT(*) FROM topups').fetchone()[0]
        pages = max((count + PER_PAGE - 1) // PER_PAGE, 1)
        offset = (page - 1) * PER_PAGE
        query = (
            'SELECT t.id, t.timestamp, u.name as user_name, t.amount '
            'FROM topups t JOIN users u ON u.id = t.user_id '
            f'ORDER BY t.timestamp DESC LIMIT {PER_PAGE} OFFSET {int(offset)}'
        )
        cur = conn.execute(query)
        items = cur.fetchall()
        conn.close()
        return render_template('topup_log.html', items=items, page=page, pages=pages)


    @app.route('/topup_log/clear', methods=['POST'])
    @login_required
    def topup_log_clear():
        conn = database.get_connection()
        conn.execute('DELETE FROM topups')
        conn.commit()
        conn.close()
        return redirect(url_for('topup_log'))

    @app.route('/topup_log/delete/<int:topup_id>', methods=['POST'])
    @login_required
    def topup_delete(topup_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM topups WHERE id=?', (topup_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('topup_log'))

    @app.route('/users/add', methods=['POST'])
    @login_required
    def user_add():
        name = request.form.get('name')
        uid = request.form.get('uid')
        balance_euro = request.form.get('balance', type=float)
        error: Optional[str] = None
        if name and uid:
            conn = database.get_connection()
            try:
                conn.execute(
                    'INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)',
                    (name, uid, int(balance_euro * 100) if balance_euro is not None else 0))
                conn.commit()
            except sqlite3.IntegrityError:
                error = 'RFID-UID bereits vergeben'
            finally:
                conn.close()
        if error:
            conn = database.get_connection()
            cur = conn.execute('SELECT * FROM users WHERE is_event=0 ORDER BY name')
            items = cur.fetchall()
            conn.close()
            return render_template('users.html', users=items, error=error)
        return redirect(url_for('users'))

    @app.route('/event_cards/add', methods=['POST'])
    @login_required
    def event_card_add():
        name = request.form.get('name')
        uid = request.form.get('uid')
        show_on_payment = 1 if request.form.get('show_on_payment') else 0
        error: Optional[str] = None
        if name and uid:
            conn = database.get_connection()
            try:
                conn.execute(
                    'INSERT INTO users (name, rfid_uid, balance, is_event, active, show_on_payment) VALUES (?, ?, 0, 1, 1, ?)',
                    (name, uid, show_on_payment),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                error = 'RFID-UID bereits vergeben'
            finally:
                conn.close()
        if error:
            conn = database.get_connection()
            cur = conn.execute('SELECT * FROM users WHERE is_event=1 ORDER BY name')
            items = cur.fetchall()
            conn.close()
            return render_template('event_cards.html', users=items, error=error)
        return redirect(url_for('event_cards'))


    @app.route('/users/topup', methods=['POST'])
    @login_required
    def users_topup():
        uid = request.form.get('uid')
        amount_euro = request.form.get('amount', type=float)
        if uid and amount_euro is not None:
            user = models.get_user_by_uid(uid)
            if user:
                cents = int(amount_euro * 100)
                models.update_balance(user.id, cents)
                models.add_topup(user.id, cents)
                return redirect(url_for('users'))
            else:
                return users(error='Unbekannte UID')
        return users(error='Ungültige Eingabe')


    @app.route('/users/delete/<int:user_id>')
    @login_required
    def user_delete(user_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('users'))

    @app.route('/event_cards/delete/<int:user_id>')
    @login_required
    def event_card_delete(user_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM users WHERE id = ? AND is_event=1', (user_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('event_cards'))

    @app.route('/event_cards/print/<int:user_id>')
    @login_required
    def event_card_print(user_id: int):
        conn = database.get_connection()
        user = conn.execute(
            'SELECT * FROM users WHERE id=? AND is_event=1', (user_id,)
        ).fetchone()
        if not user:
            conn.close()
            return redirect(url_for('event_cards'))
        cur = conn.execute(
            'SELECT t.timestamp, d.name, t.quantity, d.price '
            'FROM transactions t JOIN drinks d ON d.id = t.drink_id '
            'WHERE t.user_id=? ORDER BY t.timestamp',
            (user_id,),
        )
        items = cur.fetchall()
        conn.close()
        total = sum(r['quantity'] * r['price'] for r in items)
        return render_template('event_print.html', user=user, items=items, total=total)

    @app.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
    @login_required
    def user_edit(user_id: int):
        conn = database.get_connection()
        if request.method == 'POST':
            name = request.form.get('name')
            uid = request.form.get('uid')
            balance_euro = request.form.get('balance', type=float)
            is_event = 1 if request.form.get('is_event') else 0
            is_admin = 1 if request.form.get('is_admin') else 0
            active = 1 if request.form.get('active') else 0
            show_on_payment = 1 if request.form.get('show_on_payment') and is_event else 0
            conn.execute(
                'UPDATE users SET name=?, rfid_uid=?, balance=?, is_event=?, active=?, show_on_payment=?, is_admin=? WHERE id=?',
                (
                    name,
                    uid,
                    int(balance_euro * 100) if balance_euro is not None else 0,
                    is_event,
                    active,
                    show_on_payment,
                    is_admin,
                    user_id,
                ),
            )
            conn.commit()
            conn.close()
            return redirect(url_for('event_cards' if is_event else 'users'))
        cur = conn.execute('SELECT * FROM users WHERE id=?', (user_id,))
        item = cur.fetchone()
        conn.close()
        return render_template('user_edit.html', user=item)

    @app.route('/log')
    @login_required
    def log():
        tx_page = request.args.get('tx_page', default=1, type=int)
        restock_page = request.args.get('restock_page', default=1, type=int)

        conn = database.get_connection()
        tx_count = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        tx_pages = max((tx_count + PER_PAGE - 1) // PER_PAGE, 1)
        tx_offset = (tx_page - 1) * PER_PAGE
        query_tx = (
            'SELECT t.id, t.timestamp, u.name as user_name, d.name as drink_name, t.quantity '
            'FROM transactions t '
            'JOIN users u ON u.id = t.user_id '
            'JOIN drinks d ON d.id = t.drink_id '
            f'ORDER BY t.timestamp DESC LIMIT {PER_PAGE} OFFSET {int(tx_offset)}'
        )
        cur = conn.execute(query_tx)
        items = cur.fetchall()

        restock_count = conn.execute('SELECT COUNT(*) FROM restocks').fetchone()[0]
        restock_pages = max((restock_count + PER_PAGE - 1) // PER_PAGE, 1)
        restock_offset = (restock_page - 1) * PER_PAGE
        query_restock = (
            'SELECT r.id, r.timestamp, d.name as drink_name, r.quantity '
            'FROM restocks r JOIN drinks d ON d.id = r.drink_id '
            f'ORDER BY r.timestamp DESC LIMIT {PER_PAGE} OFFSET {int(restock_offset)}'
        )
        restocks = conn.execute(query_restock).fetchall()
        conn.close()
        return render_template(
            'log.html',
            items=items,
            restocks=restocks,
            tx_page=tx_page,
            tx_pages=tx_pages,
            restock_page=restock_page,
            restock_pages=restock_pages,
        )

    @app.route('/log/transactions_clear', methods=['POST'])
    @login_required
    def transactions_clear():
        conn = database.get_connection()
        conn.execute('DELETE FROM transactions')
        conn.commit()
        conn.close()
        return redirect(url_for('log'))

    @app.route('/log/transaction_delete/<int:tx_id>', methods=['POST'])
    @login_required
    def transaction_delete(tx_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM transactions WHERE id=?', (tx_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('log'))

    @app.route('/log/restocks_clear', methods=['POST'])
    @login_required
    def restocks_clear():
        conn = database.get_connection()
        conn.execute('DELETE FROM restocks')
        conn.commit()
        conn.close()
        return redirect(url_for('log'))

    @app.route('/log/restock_delete/<int:restock_id>', methods=['POST'])
    @login_required
    def restock_delete(restock_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM restocks WHERE id=?', (restock_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('log'))


    @app.route('/export/transactions')
    @login_required
    def export_transactions():
        conn = database.get_connection()
        cur = conn.execute(
            'SELECT t.timestamp, u.name as user_name, d.name as drink_name, t.quantity '
            'FROM transactions t '
            'JOIN users u ON u.id = t.user_id '
            'JOIN drinks d ON d.id = t.drink_id '
            'ORDER BY t.timestamp DESC')
        rows = cur.fetchall()
        conn.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['timestamp', 'user', 'drink', 'quantity'])
        for r in rows:
            writer.writerow([r['timestamp'], r['user_name'], r['drink_name'], r['quantity']])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename=transactions.csv'
        return resp

    @app.route('/export/transactions_anonymized')
    @login_required
    def export_transactions_anonymized():
        conn = database.get_connection()
        cur = conn.execute(
            'SELECT t.timestamp, d.name as drink_name, t.quantity '
            'FROM transactions t '
            'JOIN drinks d ON d.id = t.drink_id '
            'ORDER BY t.timestamp DESC'
        )
        rows = cur.fetchall()
        conn.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['timestamp', 'drink', 'quantity'])
        for r in rows:
            writer.writerow([r['timestamp'], r['drink_name'], r['quantity']])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = (
            'attachment; filename=transactions_anonymized.csv'
        )
        return resp

    @app.route('/export/inventory')
    @login_required
    def export_inventory():
        conn = database.get_connection()
        cur = conn.execute('SELECT name, price, stock FROM drinks ORDER BY name')
        rows = cur.fetchall()
        conn.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['name', 'price_euro', 'stock'])
        for r in rows:
            writer.writerow([r['name'], f"{r['price']/100:.2f}", r['stock']])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename=inventory.csv'
        return resp

    @app.route('/export/users')
    @login_required
    def export_users():
        conn = database.get_connection()
        cur = conn.execute('SELECT name, rfid_uid, balance FROM users ORDER BY name')
        rows = cur.fetchall()
        conn.close()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['name', 'uid', 'balance_euro'])
        for r in rows:
            writer.writerow([r['name'], r['rfid_uid'], f"{r['balance']/100:.2f}"])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename=users.csv'
        return resp

    @app.route('/import/users', methods=['GET', 'POST'])
    @login_required
    def import_users():
        if request.method == 'POST':
            file = request.files.get('file')
            if file and file.filename:

                raw = file.read().decode('utf-8')
                try:
                    dialect = csv.Sniffer().sniff(raw.splitlines()[0], delimiters=',;')
                except csv.Error:
                    dialect = csv.get_dialect('excel')
                reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
                conn = database.get_connection()
                for row in reader:
                    name = (row.get('name') or '').strip()
                    uid = (row.get('uid') or '').strip()
                    val = row.get('balance_euro') or row.get('balance') or '0'
                    val = val.replace(',', '.')
                    try:
                        balance = int(float(val) * 100)

                    except ValueError:
                        balance = 0
                    if not name or not uid:
                        continue
                    try:
                        conn.execute(
                            'INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)',
                            (name, uid, balance),

                        )
                    except sqlite3.IntegrityError:
                        pass
                conn.commit()
                conn.close()
            return redirect(url_for('users'))
        return render_template('import_users.html')

    @app.route('/export/restocks')
    @login_required
    def export_restocks():
        rows = models.get_restock_log()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['timestamp', 'drink', 'quantity'])
        for r in rows:
            writer.writerow([r['timestamp'], r['drink_name'], r['quantity']])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename=restocks.csv'
        return resp

    @app.route('/export/topups')
    @login_required
    def export_topups():
        rows = models.get_topup_log()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['timestamp', 'user', 'amount_euro'])
        for r in rows:
            writer.writerow([r['timestamp'], r['user_name'], f"{r['amount']/100:.2f}"])
        resp = make_response(out.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename=topups.csv'
        return resp

    @app.route('/file_logs')
    @login_required
    def file_logs():
        page = request.args.get('page', default=1, type=int)
        log_dir = Path(__file__).resolve().parents[1] / 'logs'
        files: list[Path] = []
        if log_dir.exists():
            files = sorted(log_dir.glob('log_*.txt'), reverse=True)
        total = len(files)
        pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)
        start = (page - 1) * PER_PAGE
        files = files[start : start + PER_PAGE]
        return render_template(
            'file_logs.html',
            files=[f.name for f in files],
            page=page,
            pages=pages,
        )

    @app.route('/file_logs/delete/<name>', methods=['POST'])
    @login_required
    def file_logs_delete(name: str):
        log_dir = Path(__file__).resolve().parents[1] / 'logs'
        target = log_dir / name
        if target.exists() and target.is_file():
            target.unlink()
        return redirect(url_for('file_logs'))

    return app


def main() -> None:
    database.init_db()
    app = create_app()
    template_path = Path(__file__).parent / 'templates'
    app.template_folder = str(template_path)
    app.run(host='0.0.0.0', port=8000)


if __name__ == '__main__':
    main()
