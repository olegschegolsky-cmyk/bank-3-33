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
async def transfer(data: TransferData, user: dict = Depends(get_current_user)):
    if data.amount <= 0:
        return JSONResponse(status_code=400, content={"message": "Некоректні дані для переказу."})
    recipient = database.find_user_by_login(data.recipientFullName)
    if not recipient:
        return JSONResponse(status_code=404, content={"message": "Отримувача не знайдено."})
    if recipient["id"] == user["id"]:
        return JSONResponse(status_code=400, content={"message": "Неможливо переказати кошти самому собі."})
    
    result = database.perform_transfer(user["id"], recipient["id"], data.amount, data.comment)
    if result["success"]:
        await manager.broadcast({"type": "full_update_required"}, [user["id"], recipient["id"]])
        return {"success": True, "message": result["message"]}
    return JSONResponse(status_code=400, content={"message": result["message"]})

# --- Deposits Endpoints ---
class DepositData(BaseModel):
    amount: float
    days: int

@app.post("/api/deposits")
async def create_deposit(data: DepositData, user: dict = Depends(get_current_user)):
    res = database.create_deposit(user["id"], data.amount, data.days)
    if res["success"]:
        await manager.broadcast({"type": "full_update_required"}, [user["id"]])
        return {"success": True, "message": res["message"]}
    return JSONResponse(status_code=400, content={"message": res["message"]})

class ClaimDepositData(BaseModel):
    depositId: int

@app.post("/api/deposits/claim")
async def claim_deposit(data: ClaimDepositData, user: dict = Depends(get_current_user)):
    res = database.claim_deposit(user["id"], data.depositId)
    if res["success"]:
        await manager.broadcast({"type": "full_update_required"}, [user["id"]])
        return {"success": True, "message": res["message"]}
    return JSONResponse(status_code=400, content={"message": res["message"]})

@app.get("/api/admin/deposits")
def admin_get_deposits(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    db = database.get_db()
    deps = db.execute('''
        SELECT d.*, u.full_name 
        FROM deposits d
        JOIN users u ON d.user_id = u.id
        WHERE d.status = 'active'
        ORDER BY d.start_time DESC
    ''').fetchall()
    return [dict(d) for d in deps]

class CancelDepositData(BaseModel):
    depositId: int

@app.post("/api/admin/deposits/cancel")
async def admin_cancel_deposit(data: CancelDepositData, user: dict = Depends(get_current_user)):
    if not user.get("is_admin"): return JSONResponse(status_code=403, content={})
    res = database.admin_cancel_deposit(data.depositId, user['full_name'])
    if res['success']:
        await manager.broadcast({"type": "admin_panel_update_required"})
        await manager.broadcast({"type": "full_update_required"}, [res['user_id']])
        return {"success": True}
    return JSONResponse(status_code=400, content={"message": res['message']})
# -------------------------

class CartItem(BaseModel):
    id: int
    quantity: int

class PurchaseData(BaseModel):
    cart: List[CartItem]

@app.post("/api/purchase")
async def purchase(data: PurchaseData, user: dict = Depends(get_current_user)):
    if not data.cart:
        return JSONResponse(status_code=400, content={"message": "Кошик порожній."})
    
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
            
        cursor.execute("INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)",
                       (user["id"], "purchase", -total_cost, "Магазин", f"Покупка {len(data.cart)} товарів"))
        db.commit()
        
        await manager.broadcast({"type": "full_update_required"}, [user["id"]])
        await manager.broadcast({"type": "shop_update_required"})
        return {"success": True, "message": f"Покупку на суму {total_cost:.2f} грн успішно оформлено."}
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=400, content={"message": str(e)})

@app.get("/api/admin/users")
def admin_get_users(user: dict = Depends(get_current_user)):
    db = database.get_db()
    users = db.execute('''
        SELECT u.id, u.username, u.full_name, u.dob, u.balance, u.is_blocked, u.team_id, t.name as team_name
        FROM users u
        LEFT JOIN teams t ON u.team_id = t.id
        WHERE u.is_admin = 0
        ORDER BY u.full_name
    ''').fetchall()
    return [dict(u) for u in users]

class UserCreateData(BaseModel):
    username: str
    password: str
    fullName: str
    dob: Optional[str] = None
    balance: float = 100

@app.post("/api/admin/users")
async def admin_create_user(data: UserCreateData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    try:
        cursor = db.cursor()
        cursor.execute('INSERT INTO users (username, password_hash, full_name, dob, balance) VALUES (?, ?, ?, ?, ?)',
                       (data.username, database.simple_hash(data.password), data.fullName, data.dob, data.balance))
        db.commit()
        await manager.broadcast({"type": "admin_panel_update_required"})
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
async def admin_update_user(user_id: int, data: UserUpdateData, user: dict = Depends(get_current_user)):
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
    await manager.broadcast({"type": "admin_panel_update_required"})
    await manager.broadcast({"type": "full_update_required"}, [user_id])
    return {"success": True}

class AdjustBalanceData(BaseModel):
    userId: int
    amount: float
    comment: str

@app.post("/api/admin/users/adjust-balance")
async def admin_adjust_balance(data: AdjustBalanceData, current_user: dict = Depends(get_current_user)):
    database.adjust_balance(data.userId, data.amount, data.comment, current_user["full_name"])
    await manager.broadcast({"type": "admin_panel_update_required"})
    await manager.broadcast({"type": "full_update_required"}, [data.userId])
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
async def admin_create_team(data: TeamCreateData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    try:
        cursor = db.cursor()
        cursor.execute('INSERT INTO teams (name) VALUES (?)', (data.name,))
        team_id = cursor.lastrowid
        for m in data.members:
            cursor.execute('UPDATE users SET team_id = ? WHERE id = ?', (team_id, m))
        db.commit()
        await manager.broadcast({"type": "admin_panel_update_required"})
        return JSONResponse(status_code=201, content={"id": team_id})
    except database.sqlite3.IntegrityError:
        return JSONResponse(status_code=409, content={"message": "Команда з такою назвою вже існує."})

class BulkAdjustData(BaseModel):
    teamId: int
    amount: float
    comment: str
    action: str

@app.post("/api/admin/teams/bulk-adjust")
async def admin_bulk_adjust(data: BulkAdjustData, current_user: dict = Depends(get_current_user)):
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
            cursor.execute('INSERT INTO transactions (user_id, type, amount, counterparty, comment) VALUES (?, ?, ?, ?, ?)',
                           (uid, 'admin_adjustment', final_amount, f"Масова операція ({current_user['full_name']})", data.comment))
            updated_ids.append(uid)
        db.commit()
        
        await manager.broadcast({"type": "admin_panel_update_required"})
        await manager.broadcast({"type": "full_update_required"}, updated_ids)
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
async def admin_create_shop_item(data: ShopItemData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO shop_items (name, price, discount_price, quantity, category, description, image)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (data.name, data.price, data.discountPrice, data.quantity, data.category, data.description, data.image))
    db.commit()
    await manager.broadcast({"type": "shop_update_required"})
    return JSONResponse(status_code=201, content={"id": cursor.lastrowid})

@app.put("/api/admin/shop-items/{item_id}")
async def admin_update_shop_item(item_id: int, data: ShopItemData, user: dict = Depends(get_current_user)):
    db = database.get_db()
    db.execute('''
        UPDATE shop_items SET
        name = ?, price = ?, discount_price = ?, quantity = ?, category = ?, description = ?, image = ?
        WHERE id = ?
    ''', (data.name, data.price, data.discountPrice, data.quantity, data.category, data.description, data.image, item_id))
    db.commit()
    await manager.broadcast({"type": "shop_update_required"})
    return {"success": True}

@app.delete("/api/admin/shop-items/{item_id}")
async def admin_delete_shop_item(item_id: int, user: dict = Depends(get_current_user)):
    db = database.get_db()
    db.execute('DELETE FROM shop_items WHERE id = ?', (item_id,))
    db.commit()
    await manager.broadcast({"type": "shop_update_required"})
    return {"success": True}

@app.get("/api/admin/db/download")
def download_database(user: dict = Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Доступ заборонено")
    return FileResponse(database.DB_PATH, media_type="application/octet-stream", filename="ceo_bank.db")

@app.post("/api/admin/db/upload")
async def upload_database(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Доступ заборонено")
    
    try:
        database.local.conn.close()
        delattr(database.local, "conn")
    except Exception:
        pass

    with open(database.DB_PATH, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    database.initialize_db()
    
    await manager.broadcast({"type": "full_update_required"})
    await manager.broadcast({"type": "shop_update_required"})
    await manager.broadcast({"type": "admin_panel_update_required"})
    
    return {"success": True, "message": "Базу даних успішно завантажено"}

app.mount("/", StaticFiles(directory="public", html=True), name="public")