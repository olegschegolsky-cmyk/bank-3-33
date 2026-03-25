from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict
import jwt
import database
import asyncio
import shutil

JWT_SECRET = 'your_super_secret_key_for_ceo_bank_project'

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    async def broadcast(self, message: dict, user_ids: Optional[List[int]] = None):
        if user_ids:
            for uid in user_ids:
                wss = self.active_connections.get(uid, [])
                for ws in wss:
                    try:
                        await ws.send_json(message)
                    except:
                        pass
        else:
            for wss in list(self.active_connections.values()):
                for ws in wss:
                    try:
                        await ws.send_json(message)
                    except:
                        pass

manager = ConnectionManager()

# --- ФОНОВИЙ РУХ РИНКУ КОЖНІ 30 СЕКУНД ---
async def market_simulation_task():
    while True:
        await asyncio.sleep(30)
        try:
            updated = database.simulate_market_fluctuations()
            if updated:
                await manager.broadcast({"type": "exchange_update_required"})
        except Exception as e:
            print("Market loop error:", e)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(market_simulation_task())
# ----------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    user_id = None
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "register":
                token = data.get("payload", {}).get("token")
                if token:
                    try:
                        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
                        user_id = decoded.get("id")
                        if user_id not in manager.active_connections:
                            manager.active_connections[user_id] = []
                        manager.active_connections[user_id].append(websocket)
                    except jwt.PyJWTError:
                        await websocket.close()
                        return
    except WebSocketDisconnect:
        if user_id:
            manager.disconnect(websocket, user_id)

def verify_token(authorization: str = None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user = database.find_user_by_id(decoded["id"])
        if not user:
            raise HTTPException(status_code=404)
        return dict(user)
    except jwt.PyJWTError:
        raise HTTPException(status_code=403)

async def get_current_user(authorization: Optional[str] = Header(None)):
    return verify_token(authorization)

class LoginData(BaseModel):
    login: str
    password: str

@app.post("/login")
def login(data: LoginData):
    user = database.find_user_by_login(data.login)
    hashed_password = database.simple_hash(data.password)
    if user and user["password_hash"] == hashed_password:
        if user["is_blocked"]:
            return JSONResponse(status_code=403, content={"message": "Ваш акаунт заблоковано."})
        token = jwt.encode({"id": user["id"], "username": user["username"], "is_admin": user["is_admin"]}, JWT_SECRET, algorithm="HS256")
        return {"success": True, "token": token, "isAdmin": bool(user["is_admin"])}
    return JSONResponse(status_code=401, content={"message": "Неправильний логін або пароль."})

@app.get("/api/app-data")
def get_app_data(user: dict = Depends(get_current_user)):
    return database.get_app_data(user["id"])

class TransferData(BaseModel):
    recipientFullName: str
    amount: float
    comment: str = "Приватний переказ"

@app.post("/api/transfer")
def transfer(data: TransferData, user: dict = Depends(get_current_user)):
    if data.amount <= 0:
        return JSONResponse(status_code=400, content={"message": "Некоректні дані для переказу."})
    recipient = database.find_user_by_login(data.recipientFullName)
    if not recipient:
        return JSONResponse(status_code=404, content={"message": "Отримувача не знайдено."})
    if recipient["id"] == user["id"]:
        return JSONResponse(status_code=400, content={"message": "Неможливо переказати кошти самому собі."})
    
    result = database.perform_transfer(user["id"], recipient["id"], data.amount, data.comment)
    if result["success"]:
        asyncio.run(manager.broadcast({"type": "full_update_required"}, [user["id"], recipient["id"]]))
        return {"success": True, "message": result["message"]}
    return JSONResponse(status_code=400, content={"message": result["message"]})

@app.get("/api/exchange/data")
def get_exchange_data(user: dict = Depends(get_current_user)):
    db = database.get_db()
    assets = db.execute('SELECT * FROM exchange_assets ORDER BY type, name').fetchall()
    assets_list = [dict(a) for a in assets]
    history = {}
    for asset in assets_list:
        hist = db.execute('SELECT price, timestamp FROM price_history WHERE asset_id = ? ORDER BY id DESC LIMIT 50', (asset['id'],)).fetchall()
        history[asset['id']] = [{"price": h["price"], "time": h["timestamp"]} for h in reversed(hist)]
    portfolio = db.execute('SELECT asset_id, amount FROM user_portfolio WHERE user_id = ? AND amount > 0', (user['id'],)).fetchall()
    return {
        "assets": assets_list,
        "history": history,
        "portfolio": [dict(p) for p in portfolio]
    }

class ExchangeTradeData(BaseModel):
    assetId: int
    amount: float

@app.post("/api/exchange/buy")
async def exchange_buy(data: ExchangeTradeData, user: dict = Depends(get_current_user)):
    if data.amount <= 0: return JSONResponse(status_code=400, content={"message": "Кількість > 0"})
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        asset = cursor.execute("SELECT * FROM exchange_assets WHERE id = ?", (data.assetId,)).fetchone()
        if not asset: return JSONResponse(status_code=404, content={"message": "Актив не знайдено"})
        total_cost = asset["price"] * data.amount
        u = cursor.execute("SELECT balance FROM users WHERE id = ?", (user["id"],)).fetchone()
        if u["balance"] < total_cost:
            db.rollback()
            return JSONResponse(status_code=400, content={"message": f"Недостатньо коштів. Потрібно {total_cost:.2f} грн."})
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (total_cost, user["id"]))
        port = cursor.execute("SELECT amount FROM user_portfolio WHERE user_id = ? AND asset_id = ?", (user["id"], data.assetId)).fetchone()
        if port: cursor.execute("UPDATE user_portfolio SET amount = amount + ? WHERE user_id = ? AND asset_id = ?", (data.amount, user["id"], data.assetId))
        else: cursor.execute("INSERT INTO user_portfolio (user_id, asset_id, amount) VALUES (?, ?, ?)", (user["id"], data.assetId, data.amount))
        new_price = asset["price"] * (1 + asset["volatility"])
        cursor.execute("UPDATE exchange_assets SET price = ? WHERE id = ?", (new_price, data.assetId))
        cursor.execute("INSERT INTO price_history (asset_id, price) VALUES (?, ?)", (data.assetId, new_price))
        cursor.execute('''INSERT INTO exchange_transactions (user_id, asset_id, type, amount, price_per_unit, total_cost) VALUES (?, ?, 'buy', ?, ?, ?)''', (user["id"], data.assetId, data.amount, asset["price"], total_cost))
        db.commit()
        await manager.broadcast({"type": "full_update_required"}, [user["id"]])
        await manager.broadcast({"type": "exchange_update_required"})
        return {"success": True, "message": f"Куплено {data.amount} {asset['symbol']}"}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": "Помилка: " + str(e)})

@app.post("/api/exchange/sell")
async def exchange_sell(data: ExchangeTradeData, user: dict = Depends(get_current_user)):
    if data.amount <= 0: return JSONResponse(status_code=400, content={"message": "Кількість > 0"})
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        asset = cursor.execute("SELECT * FROM exchange_assets WHERE id = ?", (data.assetId,)).fetchone()
        port = cursor.execute("SELECT amount FROM user_portfolio WHERE user_id = ? AND asset_id = ?", (user["id"], data.assetId)).fetchone()
        if not asset or not port or port["amount"] < data.amount:
            db.rollback()
            return JSONResponse(status_code=400, content={"message": "Недостатньо активу."})
        total_revenue = asset["price"] * data.amount
        cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (total_revenue, user["id"]))
        cursor.execute("UPDATE user_portfolio SET amount = amount - ? WHERE user_id = ? AND asset_id = ?", (data.amount, user["id"], data.assetId))
        new_price = max(0.01, asset["price"] * (1 - asset["volatility"]))
        cursor.execute("UPDATE exchange_assets SET price = ? WHERE id = ?", (new_price, data.assetId))
        cursor.execute("INSERT INTO price_history (asset_id, price) VALUES (?, ?)", (data.assetId, new_price))
        cursor.execute('''INSERT INTO exchange_transactions (user_id, asset_id, type, amount, price_per_unit, total_cost) VALUES (?, ?, 'sell', ?, ?, ?)''', (user["id"], data.assetId, data.amount, asset["price"], total_revenue))
        db.commit()
        await manager.broadcast({"type": "full_update_required"}, [user["id"]])
        await manager.broadcast({"type": "exchange_update_required"})
        return {"success": True, "message": f"Продано {data.amount} {asset['symbol']}"}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": "Помилка: " + str(e)})

@app.get("/api/admin/exchange")
def admin_get_exchange(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    txs = db.execute('''SELECT et.*, u.full_name, a.symbol, a.name as asset_name FROM exchange_transactions et JOIN users u ON et.user_id = u.id JOIN exchange_assets a ON et.asset_id = a.id ORDER BY et.timestamp DESC LIMIT 100''').fetchall()
    assets = db.execute('SELECT * FROM exchange_assets ORDER BY type, name').fetchall()
    return {"transactions": [dict(t) for t in txs], "assets": [dict(a) for a in assets]}

class AdminAssetUpdate(BaseModel):
    assetId: int
    price: float
    volatility: float

@app.post("/api/admin/exchange/update-asset")
async def admin_update_asset(data: AdminAssetUpdate, user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE exchange_assets SET price = ?, volatility = ? WHERE id = ?", (data.price, data.volatility, data.assetId))
    cursor.execute("INSERT INTO price_history (asset_id, price) VALUES (?, ?)", (data.assetId, data.price))
    db.commit()
    await manager.broadcast({"type": "exchange_update_required"})
    await manager.broadcast({"type": "admin_panel_update_required"})
    return {"success": True}

class AdminCreateAssetData(BaseModel):
    name: str
    symbol: str
    type: str
    price: float
    volatility: float

@app.post("/api/admin/exchange/create-asset")
async def admin_create_asset(data: AdminCreateAssetData, user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("INSERT INTO exchange_assets (name, symbol, type, price, volatility) VALUES (?, ?, ?, ?, ?)", (data.name, data.symbol, data.type, data.price, data.volatility))
        asset_id = cursor.lastrowid
        cursor.execute("INSERT INTO price_history (asset_id, price) VALUES (?, ?)", (asset_id, data.price))
        db.commit()
        await manager.broadcast({"type": "exchange_update_required"})
        await manager.broadcast({"type": "admin_panel_update_required"})
        return {"success": True}
    except database.sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={"message": "Актив з таким символом вже існує"})

class CancelExTxData(BaseModel):
    transactionId: int

@app.post("/api/admin/exchange/cancel")
async def admin_cancel_exchange_tx(data: CancelExTxData, user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        tx = cursor.execute("SELECT * FROM exchange_transactions WHERE id = ? AND status = 'completed'", (data.transactionId,)).fetchone()
        if not tx:
            db.rollback()
            return JSONResponse(status_code=400, content={"message": "Транзакцію не знайдено або вже скасовано"})
        if tx['type'] == 'buy':
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (tx['total_cost'], tx['user_id']))
            cursor.execute("UPDATE user_portfolio SET amount = amount - ? WHERE user_id = ? AND asset_id = ?", (tx['amount'], tx['user_id'], tx['asset_id']))
        else: 
            cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (tx['total_cost'], tx['user_id']))
            cursor.execute("UPDATE user_portfolio SET amount = amount + ? WHERE user_id = ? AND asset_id = ?", (tx['amount'], tx['user_id'], tx['asset_id']))
        cursor.execute("UPDATE exchange_transactions SET status = 'cancelled' WHERE id = ?", (tx['id'],))
        db.commit()
        await manager.broadcast({"type": "full_update_required"}, [tx["user_id"]])
        await manager.broadcast({"type": "exchange_update_required"})
        await manager.broadcast({"type": "admin_panel_update_required"})
        return {"success": True}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": str(e)})

class DepositData(BaseModel):
    amount: float
    days: int

@app.post("/api/deposits")
def create_deposit(data: DepositData, user: dict = Depends(get_current_user)):
    res = database.create_deposit(user["id"], data.amount, data.days)
    if res["success"]:
        asyncio.run(manager.broadcast({"type": "full_update_required"}, [user["id"]]))
        return {"success": True, "message": res["message"]}
    return JSONResponse(status_code=400, content={"message": res["message"]})

class ClaimDepositData(BaseModel):
    depositId: int

@app.post("/api/deposits/claim")
def claim_deposit(data: ClaimDepositData, user: dict = Depends(get_current_user)):
    res = database.claim_deposit(user["id"], data.depositId)
    if res["success"]:
        asyncio.run(manager.broadcast({"type": "full_update_required"}, [user["id"]]))
        return {"success": True, "message": res["message"]}
    return JSONResponse(status_code=400, content={"message": res["message"]})

@app.get("/api/admin/deposits")
def admin_get_deposits(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    deps = db.execute('''SELECT d.*, u.full_name FROM deposits d JOIN users u ON d.user_id = u.id WHERE d.status = 'active' ORDER BY d.start_time DESC''').fetchall()
    return [dict(d) for d in deps]

class CancelDepositData(BaseModel):
    depositId: int

@app.post("/api/admin/deposits/cancel")
def admin_cancel_deposit(data: CancelDepositData, user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    res = database.admin_cancel_deposit(data.depositId, user['full_name'])
    if res['success']:
        asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
        asyncio.run(manager.broadcast({"type": "full_update_required"}, [res['user_id']]))
        return {"success": True}
    return JSONResponse(status_code=400, content={"message": res['message']})

class CartItem(BaseModel):
    id: int
    quantity: int

class PurchaseData(BaseModel):
    cart: List[CartItem]

@app.post("/api/purchase")
def purchase(data: PurchaseData, user: dict = Depends(get_current_user)):
    if not data.cart: return JSONResponse(status_code=400, content={"message": "Кошик порожній."})
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        total_cost = 0
        for item in data.cart:
            db_item = cursor.execute("SELECT * FROM shop_items WHERE id = ?", (item.id,)).fetchone()
            if not db_item or db_item["quantity"] < item.quantity:
                db.rollback()
                return JSONResponse(status_code=400, content={"message": "Недостатня кількість товару."})
            price = db_item["discount_price"] if db_item["discount_price"] else db_item["price"]
            total_cost += price * item.quantity
        current_user = cursor.execute("SELECT balance FROM users WHERE id = ?", (user["id"],)).fetchone()
        if current_user["balance"] < total_cost:
            db.rollback()
            return JSONResponse(status_code=400, content={"message": "Недостатньо коштів на балансі."})
        cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (total_cost, user["id"]))
        for item in data.cart:
            cursor.execute("UPDATE shop_items SET quantity = quantity - ?, popularity = popularity + ? WHERE id = ?", (item.quantity, item.quantity, item.id))
        cursor.execute("INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)", (user["id"], "purchase", -total_cost, "Магазин", f"Покупка {len(data.cart)} товарів"))
        db.commit()
        asyncio.run(manager.broadcast({"type": "full_update_required"}, [user["id"]]))
        asyncio.run(manager.broadcast({"type": "shop_update_required"}))
        return {"success": True, "message": f"Покупку на суму {total_cost:.2f} грн успішно оформлено."}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": str(e)})

@app.get("/api/admin/users")
def admin_get_users(user: dict = Depends(get_current_user)):
    db = database.get_db()
    users = db.execute('''SELECT u.id, u.username, u.full_name, u.dob, u.balance, u.is_blocked, u.team_id, t.name as team_name FROM users u LEFT JOIN teams t ON u.team_id = t.id WHERE u.is_admin = 0 ORDER BY u.full_name''').fetchall()
    return [dict(u) for u in users]

class UserCreateData(BaseModel):
    username: str
    password: str
    fullName: str
    dob: Optional[str] = None
    balance: float = 100

@app.post("/api/admin/users")
def admin_create_user(data: UserCreateData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    try:
        cursor = db.cursor()
        cursor.execute('INSERT INTO users (username, password_hash, full_name, dob, balance) VALUES (?, ?, ?, ?, ?)', (data.username, database.simple_hash(data.password), data.fullName, data.dob, data.balance))
        db.commit()
        asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
        return JSONResponse(status_code=201, content={"id": cursor.lastrowid})
    except database.sqlite3.IntegrityError:
        return JSONResponse(status_code=409, content={"message": "Користувач з таким логіном вже існує."})

class UserUpdateData(BaseModel):
    username: str
    fullName: str
    dob: Optional[str] = None
    balance: float
    is_blocked: bool
    team_id: Optional[int] = None
    password: Optional[str] = None

@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, data: UserUpdateData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    sql = 'UPDATE users SET username = ?, full_name = ?, dob = ?, balance = ?, is_blocked = ?, team_id = ?'
    params = [data.username, data.fullName, data.dob, data.balance, 1 if data.is_blocked else 0, data.team_id]
    if data.password:
        sql += ', password_hash = ?'
        params.append(database.simple_hash(data.password))
    sql += ' WHERE id = ?'
    params.append(user_id)
    db.execute(sql, tuple(params))
    db.commit()
    asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
    asyncio.run(manager.broadcast({"type": "full_update_required"}, [user_id]))
    return {"success": True}

class AdjustBalanceData(BaseModel):
    userId: int
    amount: float
    comment: str

@app.post("/api/admin/users/adjust-balance")
def admin_adjust_balance(data: AdjustBalanceData, current_user: dict = Depends(get_current_user)):
    database.adjust_balance(data.userId, data.amount, data.comment, current_user["full_name"])
    asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
    asyncio.run(manager.broadcast({"type": "full_update_required"}, [data.userId]))
    return {"success": True}

@app.get("/api/admin/teams")
def admin_get_teams(user: dict = Depends(get_current_user)):
    db = database.get_db()
    teams = db.execute('SELECT * FROM teams').fetchall()
    return [dict(t) for t in teams]

class TeamCreateData(BaseModel):
    name: str
    members: List[int] = []

@app.post("/api/admin/teams")
def admin_create_team(data: TeamCreateData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    try:
        cursor = db.cursor()
        cursor.execute('INSERT INTO teams (name) VALUES (?)', (data.name,))
        team_id = cursor.lastrowid
        for m in data.members: cursor.execute('UPDATE users SET team_id = ? WHERE id = ?', (team_id, m))
        db.commit()
        asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
        return JSONResponse(status_code=201, content={"id": team_id})
    except database.sqlite3.IntegrityError:
        return JSONResponse(status_code=409, content={"message": "Команда з такою назвою вже існує."})

class BulkAdjustData(BaseModel):
    teamId: int
    amount: float
    comment: str
    action: str

@app.post("/api/admin/teams/bulk-adjust")
def admin_bulk_adjust(data: BulkAdjustData, current_user: dict = Depends(get_current_user)):
    final_amount = data.amount if data.action == 'add' else -data.amount
    db = database.get_db()
    cursor = db.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION")
        users_in_team = cursor.execute('SELECT id FROM users WHERE team_id = ?', (data.teamId,)).fetchall()
        if not users_in_team:
            db.rollback()
            return JSONResponse(status_code=400, content={"message": "В команді немає учасників."})
        updated_ids = []
        for u in users_in_team:
            uid = u["id"]
            cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (final_amount, uid))
            cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)', (uid, 'admin_adjustment', final_amount, f"Масова операція ({current_user['full_name']})", data.comment))
            updated_ids.append(uid)
        db.commit()
        asyncio.run(manager.broadcast({"type": "admin_panel_update_required"}))
        asyncio.run(manager.broadcast({"type": "full_update_required"}, updated_ids))
        return {"success": True, "message": f"Баланс {len(updated_ids)} учасників оновлено."}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": str(e)})

@app.get("/api/admin/shop-items")
def admin_get_shop_items(user: dict = Depends(get_current_user)):
    db = database.get_db()
    items = db.execute('SELECT * FROM shop_items ORDER BY name').fetchall()
    return [dict(i) for i in items]

class ShopItemData(BaseModel):
    name: str
    price: float
    discountPrice: Optional[float] = None
    quantity: int
    category: str
    description: str
    image: str

@app.post("/api/admin/shop-items")
def admin_create_shop_item(data: ShopItemData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    cursor = db.cursor()
    cursor.execute('''INSERT INTO shop_items (name, price, discount_price, quantity, category, description, image) VALUES (?, ?, ?, ?, ?, ?, ?)''', (data.name, data.price, data.discountPrice, data.quantity, data.category, data.description, data.image))
    db.commit()
    asyncio.run(manager.broadcast({"type": "shop_update_required"}))
    return JSONResponse(status_code=201, content={"id": cursor.lastrowid})

@app.put("/api/admin/shop-items/{item_id}")
def admin_update_shop_item(item_id: int, data: ShopItemData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    db.execute('''UPDATE shop_items SET name = ?, price = ?, discount_price = ?, quantity = ?, category = ?, description = ?, image = ? WHERE id = ?''', (data.name, data.price, data.discountPrice, data.quantity, data.category, data.description, data.image, item_id))
    db.commit()
    asyncio.run(manager.broadcast({"type": "shop_update_required"}))
    return {"success": True}

@app.delete("/api/admin/shop-items/{item_id}")
def admin_delete_shop_item(item_id: int, user: dict = Depends(get_current_user)):
    db = database.get_db()
    db.execute('DELETE FROM shop_items WHERE id = ?', (item_id,))
    db.commit()
    asyncio.run(manager.broadcast({"type": "shop_update_required"}))
    return {"success": True}

@app.get("/api/admin/db/download")
def download_database(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): raise HTTPException(status_code=403, detail="Доступ заборонено")
    return FileResponse(database.DB_PATH, media_type="application/octet-stream", filename="ceo_bank.db")

@app.post("/api/admin/db/upload")
async def upload_database(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): raise HTTPException(status_code=403, detail="Доступ заборонено")
    try:
        database.local.conn.close()
        delattr(database.local, "conn")
    except Exception: pass
    with open(database.DB_PATH, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    database.initialize_db()
    await manager.broadcast({"type": "full_update_required"})
    await manager.broadcast({"type": "shop_update_required"})
    await manager.broadcast({"type": "exchange_update_required"})
    await manager.broadcast({"type": "admin_panel_update_required"})
    return {"success": True, "message": "Базу даних успішно завантажено"}

app.mount("/", StaticFiles(directory="public", html=True), name="public")