import sqlite3
import os
import threading

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
    if len(s) == 0:
        return str(hash_val)
    
    for char in s:
        char_code = ord(char)
        hash_val = ((hash_val << 5) - hash_val) + char_code
        hash_val = hash_val & 0xFFFFFFFF
        if hash_val >= 0x80000000:
            hash_val -= 0x100000000
            
    return str(hash_val)

def initialize_db():
    print('Initializing database schema...')
    db = get_db()
    cursor = db.cursor()
    
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            dob TEXT DEFAULT '01.01.2000',
            balance REAL DEFAULT 100,
            is_admin INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            team_id INTEGER,
            FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL
        );
        
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            counterparty TEXT,
            comment TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );
        
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            discount_price REAL,
            quantity INTEGER NOT NULL,
            category TEXT,
            description TEXT,
            image TEXT,
            popularity INTEGER DEFAULT 0
        );
    """)

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN dob TEXT DEFAULT '01.01.2000'")
    except sqlite3.OperationalError:
        pass
    
    users_to_create = [
        {'username': 'user1', 'fullName': 'Бицюк Ярослав', 'password': 'Yaroslav7xK'},
        {'username': 'user2', 'fullName': 'Борович Роман', 'password': 'Roman2mP'},
        {'username': 'user3', 'fullName': 'Будзан Остап', 'password': 'Ostap9vL'},
        {'username': 'user4', 'fullName': 'Бурак Маркіян', 'password': 'Markiian4bN'},
        {'username': 'user5', 'fullName': 'Вотінцев Владислав', 'password': 'Vladyslav8cR'},
        {'username': 'user6', 'fullName': 'Городечна Анна', 'password': 'Anna3zT'},
        {'username': 'user7', 'fullName': 'Грет Маркіян', 'password': 'Markiian5fW'},
        {'username': 'user8', 'fullName': 'Грет Матвій', 'password': 'Matvii2kQ'},
        {'username': 'user9', 'fullName': 'Грищук Настя', 'password': 'Nastia8jD'},
        {'username': 'user10', 'fullName': 'Грищук Наташа', 'password': 'Natasha4hM'},
        {'username': 'user11', 'fullName': 'Ґудзик Ліза', 'password': 'Liza9pB'},
        {'username': 'user12', 'fullName': 'Ґудзик Тимофій', 'password': 'Tymofii3xC'},
        {'username': 'user13', 'fullName': 'Демидівка Богдан', 'password': 'Bohdan6nV'},
        {'username': 'user14', 'fullName': 'Дяків Андрій', 'password': 'Andrii7gL'},
        {'username': 'user15', 'fullName': 'Дяків Каріна', 'password': 'Karina2mW'},
        {'username': 'user16', 'fullName': 'Емха Устина', 'password': 'Ustyna5kR'},
        {'username': 'user17', 'fullName': 'Задорожний Андрій', 'password': 'Andrii9tP'},
        {'username': 'user18', 'fullName': 'Задорожний Назар', 'password': 'Nazar4bC'},
        {'username': 'user19', 'fullName': 'Заремба Дмитро', 'password': 'Dmytro8xN'},
        {'username': 'user20', 'fullName': 'Заремба Евеліна', 'password': 'Evelina3vF'},
        {'username': 'user21', 'fullName': 'Козловський Ігор', 'password': 'Ihor7jD'},
        {'username': 'user22', 'fullName': 'Костецький Михайло', 'password': 'Mykhailo2qL'},
        {'username': 'user23', 'fullName': 'Кривець Олексій', 'password': 'Oleksii6cW'},
        {'username': 'user24', 'fullName': 'Лилк Ігор', 'password': 'Ihor9mB'},
        {'username': 'user25', 'fullName': 'Новікова Анастасія', 'password': 'Anastasiia4hK'},
        {'username': 'user26', 'fullName': 'Округін Матвій', 'password': 'Matvii8pT'},
        {'username': 'user27', 'fullName': 'Округіна Віра', 'password': 'Vira5nR'},
        {'username': 'user28', 'fullName': 'Округіна Надія', 'password': 'Nadiia2fV'},
        {'username': 'user29', 'fullName': 'Радкевич Майя', 'password': 'Maia7xM'},
        {'username': 'user30', 'fullName': 'Реуцький Ілля', 'password': 'Illia3bQ'},
        {'username': 'user31', 'fullName': 'Реуцький Микита', 'password': 'Mykyta9kL'},
        {'username': 'user32', 'fullName': 'Риби Марко', 'password': 'Marko4zC'},
        {'username': 'user33', 'fullName': 'Риби Матвій', 'password': 'Matvii6vN'},
        {'username': 'user34', 'fullName': 'Струк Дмитро', 'password': 'Dmytro2jP'},
        {'username': 'user35', 'fullName': 'Фурльовська Христина', 'password': 'Khrystyna8mF'},
        {'username': 'user36', 'fullName': 'Фурльовський Марк', 'password': 'Mark5tW'},
        {'username': 'user37', 'fullName': 'Фурльовський Маркіян', 'password': 'Markiian9cL'},
        {'username': 'user38', 'fullName': 'Чума Ярослав', 'password': 'Yaroslav3rD'}
    ]

    admin_user = cursor.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin_user:
        cursor.execute(
            'INSERT INTO users (username, password_hash, full_name, is_admin, balance) VALUES (?, ?, ?, ?, ?)',
            ('admin', simple_hash('admin123'), 'Головний Адміністратор', 1, 999999)
        )
    
    for user in users_to_create:
        username = user['username']
        existing = cursor.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if not existing:
            cursor.execute(
                'INSERT INTO users (username, password_hash, full_name, dob, is_admin) VALUES (?, ?, ?, ?, ?)',
                (username, simple_hash(user['password']), user['fullName'], '01.01.2000', 0)
            )
            
    db.commit()
    print('Database initialized successfully.')

initialize_db()

def find_user_by_login(login: str):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE username = ?', (login,)).fetchone()

def find_user_by_id(user_id: int):
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

def get_app_data(user_id: int):
    db = get_db()
    
    current_user = db.execute('''
        SELECT u.id, u.username, u.full_name, u.dob, u.balance, t.name as team_name
        FROM users u
        LEFT JOIN teams t ON u.team_id = t.id
        WHERE u.id = ?
    ''', (user_id,)).fetchone()
    
    transactions = db.execute('''
        SELECT id, type, amount, counterparty, comment, timestamp 
        FROM transactions 
        WHERE user_id = ? 
        ORDER BY timestamp DESC
    ''', (user_id,)).fetchall()
    
    shop_items = db.execute('SELECT * FROM shop_items ORDER BY popularity DESC, name').fetchall()
    
    leaderboard = db.execute('''
        SELECT u.full_name, u.balance, t.name as team_name 
        FROM users u
        LEFT JOIN teams t ON u.team_id = t.id
        WHERE u.is_admin = 0
        ORDER BY u.balance DESC
    ''').fetchall()
    
    return {
        "currentUser": dict(current_user) if current_user else None,
        "transactions": [dict(t) for t in transactions],
        "shopItems": [dict(i) for i in shop_items],
        "leaderboard": [dict(l) for l in leaderboard]
    }

def perform_transfer(from_user_id: int, to_user_id: int, amount: float, comment: str):
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute('BEGIN TRANSACTION')
        from_user = cursor.execute('SELECT balance, full_name FROM users WHERE id = ?', (from_user_id,)).fetchone()
        
        if from_user['balance'] < amount:
            db.rollback()
            return {"success": False, "message": "Недостатньо коштів."}
            
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, from_user_id))
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, to_user_id))
        
        to_user_name = cursor.execute('SELECT full_name FROM users WHERE id = ?', (to_user_id,)).fetchone()['full_name']
        from_user_name = from_user['full_name']
        
        cursor.execute('''
            INSERT INTO transactions (user_id, type, amount, counterparty, comment) 
            VALUES (?, ?, ?, ?, ?)
        ''', (from_user_id, 'transfer', -amount, to_user_name, comment))
        
        cursor.execute('''
            INSERT INTO transactions (user_id, type, amount, counterparty, comment) 
            VALUES (?, ?, ?, ?, ?)
        ''', (to_user_id, 'transfer', amount, from_user_name, comment))
        
        db.commit()
        return {"success": True, "message": "Переказ успішний"}
    except Exception as e:
        db.rollback()
        raise e

def adjust_balance(user_id: int, amount: float, comment: str, admin_name: str):
    db = get_db()
    cursor = db.cursor()
    
    try:
        cursor.execute('BEGIN TRANSACTION')
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
        cursor.execute('''
            INSERT INTO transactions (user_id, type, amount, counterparty, comment) 
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, 'admin_adjustment', amount, admin_name, comment))
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        raise e