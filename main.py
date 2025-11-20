import os
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any
from database import db
from datetime import datetime
import hashlib

app = FastAPI(title="Hyper Commerce API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VERTICALS = ["grocery", "food", "shopping"]

# -------------------------------
# Models
# -------------------------------
class RegisterPayload(BaseModel):
    name: str
    email: EmailStr
    password: str

class LoginPayload(BaseModel):
    email: EmailStr
    password: str

class CartItemPayload(BaseModel):
    product_id: str
    quantity: int = Field(ge=1)

class PlaceOrderPayload(BaseModel):
    address: str
    payment_method: str

# -------------------------------
# Utilities
# -------------------------------

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

async def current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        scheme, token = authorization.split(" ")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Token is user_id (demo only)
    from bson import ObjectId
    user = db.user.find_one({"_id": ObjectId(token)})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user")
    user["id"] = str(user.pop("_id"))
    return user

# -------------------------------
# Seed Data on Startup
# -------------------------------

def ensure_seed():
    # Create indexes
    db.user.create_index("email", unique=True)
    db.product.create_index([("vertical", 1), ("category_slug", 1)])

    # If products already seeded, skip
    if db.product.count_documents({}) > 0:
        return

    import random

    def make_slug(s: str) -> str:
        return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())

    for vertical in VERTICALS:
        # 6 categories
        categories = [
            f"{vertical.title()} Essentials",
            f"Top Picks in {vertical.title()}",
            f"Best Sellers {vertical.title()}",
            f"New in {vertical.title()}",
            f"Popular {vertical.title()}",
            f"Budget {vertical.title()}",
        ]
        category_docs = []
        for c in categories:
            cat_doc = {
                "name": c,
                "slug": make_slug(c),
                "vertical": vertical,
                "created_at": datetime.utcnow(),
            }
            category_docs.append(cat_doc)
        db.category.insert_many(category_docs)

        # 4 vendors
        vendors_raw = [
            f"{vertical.title()} Hub",
            f"{vertical.title()} Express",
            f"{vertical.title()} Mart",
            f"{vertical.title()} Bazaar",
        ]
        vendor_docs = []
        for v in vendors_raw:
            vendor_docs.append({
                "name": v,
                "slug": make_slug(v),
                "vertical": vertical,
                "rating": round(3.8 + random.random()*1.2, 1),
                "delivery_eta": random.choice(["10-20 min", "20-30 min", "30-40 min", "2-4 days"]),
                "created_at": datetime.utcnow(),
            })
        db.vendor.insert_many(vendor_docs)

        # 10 products
        product_docs = []
        for i in range(1, 11):
            cat = random.choice(category_docs)
            ven = random.choice(vendor_docs)
            product_docs.append({
                "title": f"{vertical.title()} Item {i}",
                "description": f"High-quality {vertical} product #{i}",
                "price": round(random.uniform(2.0, 199.0), 2),
                "image": f"https://picsum.photos/seed/{vertical}-{i}/400/300",
                "vertical": vertical,
                "category": cat["name"],
                "category_slug": cat["slug"],
                "vendor": ven["name"],
                "in_stock": True,
                "rating": round(3.5 + random.random()*1.5, 1),
                "created_at": datetime.utcnow(),
            })
        db.product.insert_many(product_docs)


@app.on_event("startup")
async def on_startup():
    ensure_seed()

# -------------------------------
# Basic
# -------------------------------
@app.get("/")
def read_root():
    return {"message": "Hyper Commerce Backend running"}

@app.get("/verticals")
def get_verticals():
    return {"verticals": VERTICALS}

# -------------------------------
# Auth
# -------------------------------
@app.post("/auth/register")
def register(payload: RegisterPayload):
    existing = db.user.find_one({"email": payload.email})
    if existing:
        raise HTTPException(400, "Email already registered")
    user = {
        "name": payload.name,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "created_at": datetime.utcnow(),
        "addresses": [],
    }
    user_id = db.user.insert_one(user).inserted_id
    return {"token": str(user_id), "user": {"id": str(user_id), "name": user["name"], "email": user["email"]}}

@app.post("/auth/login")
def login(payload: LoginPayload):
    u = db.user.find_one({"email": payload.email})
    if not u or u.get("password_hash") != hash_password(payload.password):
        raise HTTPException(401, "Invalid credentials")
    return {"token": str(u["_id"]), "user": {"id": str(u["_id"]), "name": u["name"], "email": u["email"]}}

# -------------------------------
# Catalog
# -------------------------------
@app.get("/home")
def home():
    data = {}
    for v in VERTICALS:
        cats = list(db.category.find({"vertical": v}).limit(6))
        vends = list(db.vendor.find({"vertical": v}).limit(4))
        prods = list(db.product.find({"vertical": v}).limit(10))
        def norm(x):
            x["id"] = str(x.pop("_id"))
            return x
        data[v] = {
            "categories": [norm(c) for c in cats],
            "vendors": [norm(ve) for ve in vends],
            "products": [norm(p) for p in prods],
        }
    return data

@app.get("/products")
def list_products(vertical: str, category_slug: Optional[str] = None, q: Optional[str] = None):
    if vertical not in VERTICALS:
        raise HTTPException(400, "Invalid vertical")
    query: Dict[str, Any] = {"vertical": vertical}
    if category_slug:
        query["category_slug"] = category_slug
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    items = list(db.product.find(query).limit(100))
    for x in items:
        x["id"] = str(x.pop("_id"))
    return {"items": items}

# -------------------------------
# Cart
# -------------------------------
@app.get("/cart")
async def get_cart(user=Depends(current_user)):
    items = list(db.cart.find({"user_id": user["id"]}))
    for x in items:
        x["id"] = str(x.pop("_id"))
    return {"items": items}

@app.post("/cart")
async def add_to_cart(payload: CartItemPayload, user=Depends(current_user)):
    from bson import ObjectId
    prod = db.product.find_one({"_id": ObjectId(payload.product_id)})
    if not prod:
        raise HTTPException(404, "Product not found")
    existing = db.cart.find_one({"user_id": user["id"], "product_id": payload.product_id})
    if existing:
        db.cart.update_one({"_id": existing["_id"]}, {"$inc": {"quantity": payload.quantity}, "$set": {"updated_at": datetime.utcnow()}})
        cart_id = existing["_id"]
    else:
        cart_id = db.cart.insert_one({
            "user_id": user["id"],
            "product_id": payload.product_id,
            "title": prod["title"],
            "price": prod["price"],
            "image": prod.get("image"),
            "quantity": payload.quantity,
            "vertical": prod["vertical"],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }).inserted_id
    return {"id": str(cart_id)}

@app.delete("/cart/{item_id}")
async def remove_cart_item(item_id: str, user=Depends(current_user)):
    from bson import ObjectId
    res = db.cart.delete_one({"_id": ObjectId(item_id), "user_id": user["id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Item not found")
    return {"ok": True}

# -------------------------------
# Orders
# -------------------------------
@app.post("/orders")
async def place_order(payload: PlaceOrderPayload, user=Depends(current_user)):
    items = list(db.cart.find({"user_id": user["id"]}))
    if not items:
        raise HTTPException(400, "Cart is empty")
    total = sum(i["price"] * i["quantity"] for i in items)
    order = {
        "user_id": user["id"],
        "items": [
            {
                "product_id": i["product_id"],
                "title": i["title"],
                "price": i["price"],
                "image": i.get("image"),
                "quantity": i["quantity"],
                "vertical": i["vertical"],
            }
            for i in items
        ],
        "total": round(total, 2),
        "address": payload.address,
        "payment_method": payload.payment_method,
        "status": "placed",
        "created_at": datetime.utcnow(),
        "order_number": f"ORD-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
    }
    order_id = db.order.insert_one(order).inserted_id
    db.cart.delete_many({"user_id": user["id"]})
    return {"id": str(order_id), "order_number": order["order_number"], "total": order["total"]}

@app.get("/orders")
async def my_orders(user=Depends(current_user)):
    orders = list(db.order.find({"user_id": user["id"]}).sort("created_at", -1))
    for o in orders:
        o["id"] = str(o.pop("_id"))
    return {"orders": orders}

# -------------------------------
# Health
# -------------------------------
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["database_url"] = "✅ Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
