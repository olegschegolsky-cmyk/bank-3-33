import sqlite3
import os
import threading
import random

DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(__file__), 'ceo_bank.db'))

local = threading.local()

def get_db():
    if not hasattr(local, "conn"):
        local.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20.0)
        local.conn.row_factory = sqlite3.Row
        local.conn.execute('PRAGMA journal_mode=WAL;')
    return local.conn

def simple_hash(s: str) -> str:
    hash_val = 0
    if len(s) == 0: return str(hash_val)
    for char in s:
        char_code = ord(char)
        hash_val = ((hash_val << 5) - hash_val) + char_code
        hash_val = hash_val & 0xFFFFFFFF
        if hash_val >= 0x80000000: hash_val -= 0x100000000
    return str(hash_val)

def initialize_db():
    db = get_db()
    cursor = db.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL, dob TEXT DEFAULT '01.01.2000', balance REAL DEFAULT 100,
            is_admin INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0, team_id INTEGER,
            FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, type TEXT NOT NULL,
            amount REAL NOT NULL, counterparty TEXT, comment TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price REAL NOT NULL,
            discount_price REAL, quantity INTEGER NOT NULL, category TEXT, description TEXT,
            image TEXT, popularity INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount REAL NOT NULL,
            expected_payout REAL NOT NULL, start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            end_time DATETIME NOT NULL, status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS exchange_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, symbol TEXT UNIQUE NOT NULL,
            type TEXT NOT NULL, price REAL NOT NULL, volatility REAL DEFAULT 0.005
        );
        CREATE TABLE IF NOT EXISTS user_portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0, UNIQUE(user_id, asset_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY (asset_id) REFERENCES exchange_assets (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL, price REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (asset_id) REFERENCES exchange_assets (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS exchange_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, asset_id INTEGER NOT NULL,
            type TEXT NOT NULL, amount REAL NOT NULL, price_per_unit REAL NOT NULL, total_cost REAL NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'completed',
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE, FOREIGN KEY (asset_id) REFERENCES exchange_assets (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS admin_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL,
            reward REAL NOT NULL, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS completed_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, task_key TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL,
            asset_id INTEGER, effect_percent REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asset_id) REFERENCES exchange_assets (id) ON DELETE SET NULL
        );
        /* НОВА ТАБЛИЦЯ ДЛЯ ЗАЯВОК НА ЗАВДАННЯ */
        CREATE TABLE IF NOT EXISTS task_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending', timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (task_id) REFERENCES admin_tasks (id) ON DELETE CASCADE
        );
    """)
    try: cursor.execute("ALTER TABLE users ADD COLUMN dob TEXT DEFAULT '01.01.2000'")
    except sqlite3.OperationalError: pass
    
    admin_user = cursor.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin_user:
        cursor.execute('INSERT INTO users (username, password_hash, full_name, is_admin, balance) VALUES (?, ?, ?, ?, ?)', ('admin', simple_hash('admin123'), 'Головний Адміністратор', 1, 999999))
    
    initial_assets = [('Bitcoin', 'BTC', 'crypto', 1500.0, 0.02), ('CEO Coin', 'CEO', 'crypto', 10.0, 0.05), ('Apple', 'AAPL', 'stock', 300.0, 0.01), ('Tesla', 'TSLA', 'stock', 250.0, 0.015)]
    for asset in initial_assets:
        if not cursor.execute('SELECT id FROM exchange_assets WHERE symbol = ?', (asset[1],)).fetchone():
            cursor.execute('INSERT INTO exchange_assets (name, symbol, type, price, volatility) VALUES (?, ?, ?, ?, ?)', asset)
            cursor.execute('INSERT INTO price_history (asset_id, price) VALUES (?, ?)', (cursor.lastrowid, asset[3]))
    db.commit()

initialize_db()

def simulate_market_fluctuations():
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        updated = False
        for asset in cursor.execute("SELECT id, price, volatility FROM exchange_assets").fetchall():
            new_price = max(0.01, asset['price'] * (1 + random.uniform(-asset['volatility'], asset['volatility']) * 0.7))
            cursor.execute("UPDATE exchange_assets SET price = ? WHERE id = ?", (new_price, asset['id']))
            cursor.execute("INSERT INTO price_history (asset_id, price) VALUES (?, ?)", (asset['id'], new_price))
            cursor.execute("DELETE FROM price_history WHERE asset_id = ? AND id NOT IN (SELECT id FROM price_history WHERE asset_id = ? ORDER BY timestamp DESC LIMIT 100)", (asset['id'], asset['id']))
            updated = True
        db.commit()
        return updated
    except Exception:
        db.rollback()
        return False

def find_user_by_login(login: str): return get_db().execute('SELECT * FROM users WHERE username = ?', (login,)).fetchone()
def find_user_by_id(user_id: int): return get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

def get_app_data(user_id: int):
    db = get_db()
    current_user = db.execute('SELECT u.id, u.username, u.full_name, u.dob, u.balance, t.name as team_name FROM users u LEFT JOIN teams t ON u.team_id = t.id WHERE u.id = ?', (user_id,)).fetchone()
    transactions = db.execute('SELECT id, type, amount, counterparty, comment, timestamp FROM transactions WHERE user_id = ? ORDER BY timestamp DESC', (user_id,)).fetchall()
    shop_items = db.execute('SELECT * FROM shop_items ORDER BY popularity DESC, name').fetchall()
    leaderboard = db.execute('SELECT u.full_name, u.balance, t.name as team_name FROM users u LEFT JOIN teams t ON u.team_id = t.id WHERE u.is_admin = 0 ORDER BY u.balance DESC').fetchall()
    deposits = db.execute('SELECT * FROM deposits WHERE user_id = ? ORDER BY start_time DESC', (user_id,)).fetchall()
    
    completed_tasks = db.execute("SELECT task_key FROM completed_tasks WHERE user_id = ?", (user_id,)).fetchall()
    admin_tasks = db.execute("SELECT * FROM admin_tasks WHERE is_active = 1").fetchall()
    pending_tasks = db.execute("SELECT task_id FROM task_requests WHERE user_id = ? AND status = 'pending'", (user_id,)).fetchall()

    return {
        "currentUser": dict(current_user) if current_user else None,
        "transactions": [dict(t) for t in transactions],
        "shopItems": [dict(i) for i in shop_items],
        "leaderboard": [dict(l) for l in leaderboard],
        "deposits": [dict(d) for d in deposits],
        "completedTasks": [c['task_key'] for c in completed_tasks],
        "adminTasks": [dict(t) for t in admin_tasks],
        "pendingTasks": [p['task_id'] for p in pending_tasks]
    }

def perform_transfer(from_user_id: int, to_user_id: int, amount: float, comment: str):
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute('BEGIN TRANSACTION')
        from_user = cursor.execute('SELECT balance, full_name FROM users WHERE id = ?', (from_user_id,)).fetchone()
        if from_user['balance'] < amount: db.rollback(); return {"success": False, "message": "Недостатньо коштів."}
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, from_user_id))
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, to_user_id))
        to_user_name = cursor.execute('SELECT full_name FROM users WHERE id = ?', (to_user_id,)).fetchone()['full_name']
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (from_user_id, 'transfer', -amount, to_user_name, comment))
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (to_user_id, 'transfer', amount, from_user['full_name'], comment))
        if amount >= 100 and not cursor.execute("SELECT id FROM completed_tasks WHERE user_id = ? AND task_key = 'auto_transfer'", (from_user_id,)).fetchone():
            cursor.execute("INSERT INTO completed_tasks (user_id, task_key) VALUES (?, 'auto_transfer')", (from_user_id,))
            cursor.execute("UPDATE users SET balance = balance + 200 WHERE id = ?", (from_user_id,))
            cursor.execute("INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, 'task_reward', 200, 'Система', 'Нагорода за завдання: Перший переказ')", (from_user_id,))
        db.commit(); return {"success": True, "message": "Переказ успішний"}
    except Exception as e: db.rollback(); raise e

def adjust_balance(user_id: int, amount: float, comment: str, admin_name: str):
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute('BEGIN TRANSACTION')
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (user_id, 'admin_adjustment', amount, admin_name, comment))
        db.commit(); return {"success": True}
    except Exception as e: db.rollback(); raise e

def create_deposit(user_id: int, amount: float, days: int):
    rates = {1: 0.07, 2: 0.15, 3: 0.24, 4: 0.34, 5: 0.45}
    if days not in rates: return {"success": False, "message": "Недопустимий термін."}
    if amount <= 0: return {"success": False, "message": "Сума має бути більшою за 0."}
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute('BEGIN TRANSACTION')
        if cursor.execute('SELECT balance FROM users WHERE id = ?', (user_id,)).fetchone()['balance'] < amount: db.rollback(); return {"success": False, "message": "Недостатньо коштів."}
        payout = amount + (amount * rates[days])
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
        cursor.execute('''INSERT INTO deposits (user_id, amount, expected_payout, end_time) VALUES (?, ?, ?, datetime('now', ?))''', (user_id, amount, payout, f'+{days} days'))
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (user_id, 'deposit', -amount, 'Банк', f'Депозит на {days} дн.'))
        if amount >= 150 and not cursor.execute("SELECT id FROM completed_tasks WHERE user_id = ? AND task_key = 'auto_deposit'", (user_id,)).fetchone():
            cursor.execute("INSERT INTO completed_tasks (user_id, task_key) VALUES (?, 'auto_deposit')", (user_id,))
            cursor.execute("UPDATE users SET balance = balance + 100 WHERE id = ?", (user_id,))
            cursor.execute("INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, 'task_reward', 100, 'Система', 'Нагорода за завдання: Перший депозит')", (user_id,))
        db.commit(); return {"success": True, "message": "Депозит відкрито."}
    except Exception as e: db.rollback(); raise e

def claim_deposit(user_id: int, deposit_id: int):
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute('BEGIN TRANSACTION')
        dep = cursor.execute("SELECT * FROM deposits WHERE id = ? AND user_id = ? AND status = 'active'", (deposit_id, user_id)).fetchone()
        if not dep: db.rollback(); return {"success": False, "message": "Депозит не знайдено."}
        if not cursor.execute("SELECT datetime('now') >= ?", (dep['end_time'],)).fetchone()[0]: db.rollback(); return {"success": False, "message": "Час ще не вийшов."}
        payout = dep['expected_payout']
        cursor.execute("UPDATE deposits SET status = 'completed' WHERE id = ?", (deposit_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (payout, user_id))
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (user_id, 'deposit_payout', payout, 'Банк', 'Виплата'))
        db.commit(); return {"success": True, "message": f"Виплачено {payout:.2f} грн!"}
    except Exception as e: db.rollback(); raise e

def admin_cancel_deposit(deposit_id: int, admin_name: str):
    db = get_db(); cursor = db.cursor()
    try:
        cursor.execute('BEGIN TRANSACTION')
        dep = cursor.execute("SELECT * FROM deposits WHERE id = ? AND status = 'active'", (deposit_id,)).fetchone()
        if not dep: db.rollback(); return {"success": False, "message": "Депозит не знайдено."}
        user_id = dep['user_id']; amount = dep['amount']
        cursor.execute("UPDATE deposits SET status = 'cancelled' WHERE id = ?", (deposit_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (user_id, 'admin_adjustment', amount, admin_name, 'Скасування депозиту'))
        db.commit(); return {"success": True, "user_id": user_id}
    except Exception as e: db.rollback(); raise e