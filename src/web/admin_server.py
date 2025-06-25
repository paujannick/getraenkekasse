from __future__ import annotations

from functools import wraps
from pathlib import Path
from typing import Optional

from .. import admin_auth

from flask import Flask, redirect, render_template, request, session, url_for

from flask import jsonify


from .. import database, models


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = 'change-me'

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
        return jsonify({'uid': uid or ''})


    @app.route('/', methods=['GET'])
    def index():
        if not session.get('user'):
            return redirect(url_for('login'))
        return render_template('index.html')


    @app.route('/refresh', methods=['POST'])
    @login_required
    def refresh():
        database.touch_refresh_flag()
        return redirect(url_for('index'))

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
    def drinks():
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM drinks ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('drinks.html', drinks=items)

    @app.route('/drinks/add', methods=['POST'])
    @login_required
    def drink_add():
        name = request.form.get('name')
        price_euro = request.form.get('price', type=float)
        stock = request.form.get('stock', type=int)
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
            conn.execute(
                'INSERT INTO drinks (name, price, stock, image) VALUES (?, ?, ?, ?)',
                (name, price, stock or 0, image_path))
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
            image_path = request.form.get('current_image') or None
            image_file = request.files.get('image')
            if image_file and image_file.filename:
                image_dir = Path(__file__).resolve().parent.parent / 'data' / 'images'
                image_dir.mkdir(parents=True, exist_ok=True)
                dest = image_dir / image_file.filename
                image_file.save(dest)
                image_path = str(dest)
            conn.execute(
                'UPDATE drinks SET name=?, price=?, stock=?, image=? WHERE id=?',
                (name, int(price_euro * 100), stock or 0, image_path, drink_id))
            conn.commit()
            conn.close()
            database.touch_refresh_flag()
            return redirect(url_for('drinks'))
        cur = conn.execute('SELECT * FROM drinks WHERE id=?', (drink_id,))
        item = cur.fetchone()
        conn.close()
        return render_template('drink_edit.html', drink=item)

    @app.route('/users')
    @login_required
    def users():
        conn = database.get_connection()
        cur = conn.execute('SELECT * FROM users ORDER BY name')
        items = cur.fetchall()
        conn.close()
        return render_template('users.html', users=items)

    @app.route('/users/add', methods=['POST'])
    @login_required
    def user_add():
        name = request.form.get('name')
        uid = request.form.get('uid')
        balance = request.form.get('balance', type=int)
        if name and uid:
            conn = database.get_connection()
            conn.execute(
                'INSERT INTO users (name, rfid_uid, balance) VALUES (?, ?, ?)',
                (name, uid, balance or 0))
            conn.commit()
            conn.close()
        return redirect(url_for('users'))


    @app.route('/users/topup', methods=['POST'])
    @login_required
    def users_topup():
        uid = request.form.get('uid')
        amount_euro = request.form.get('amount', type=float)
        if uid and amount_euro is not None:
            user = models.get_user_by_uid(uid)
            if user:
                models.update_balance(user.id, int(amount_euro * 100))
        return redirect(url_for('users'))


    @app.route('/users/delete/<int:user_id>')
    @login_required
    def user_delete(user_id: int):
        conn = database.get_connection()
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('users'))

    @app.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
    @login_required
    def user_edit(user_id: int):
        conn = database.get_connection()
        if request.method == 'POST':
            name = request.form.get('name')
            uid = request.form.get('uid')
            balance = request.form.get('balance', type=int)
            conn.execute(
                'UPDATE users SET name=?, rfid_uid=?, balance=? WHERE id=?',
                (name, uid, balance or 0, user_id))
            conn.commit()
            conn.close()
            return redirect(url_for('users'))
        cur = conn.execute('SELECT * FROM users WHERE id=?', (user_id,))
        item = cur.fetchone()
        conn.close()
        return render_template('user_edit.html', user=item)

    @app.route('/log')
    @login_required
    def log():
        conn = database.get_connection()
        cur = conn.execute(
            'SELECT t.timestamp, u.name as user_name, d.name as drink_name, t.quantity '
            'FROM transactions t '
            'JOIN users u ON u.id = t.user_id '
            'JOIN drinks d ON d.id = t.drink_id '
            'ORDER BY t.timestamp DESC LIMIT 100')
        items = cur.fetchall()
        conn.close()
        return render_template('log.html', items=items)

    return app


def main() -> None:
    database.init_db()
    app = create_app()
    template_path = Path(__file__).parent / 'templates'
    app.template_folder = str(template_path)
    app.run(host='0.0.0.0', port=8000)


if __name__ == '__main__':
    main()
