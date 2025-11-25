import sqlite3
import shutil
import os
from datetime import date, timedelta
import httpx 
from fastapi import FastAPI, Request, File, UploadFile, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pathlib import Path
from datetime import datetime
import random 

# --- 設定 ---
# 💡 修正: ローカル実行用にデータ保存先をプロジェクトフォルダ内の 'app_data' に設定
APP_DATA_DIR = Path("./app_data")
DB_NAME = APP_DATA_DIR / "condiments.db"
UPLOAD_DIR = APP_DATA_DIR / "uploads"
# 期限切れが近いと見なす日数
EXPIRY_THRESHOLD_DAYS = 7 

# --- レシピAPI設定 ---
# ⚠️ 注意: 必ずご自身の有効なIDに置き換えてください
RAKUTEN_APP_ID = "YOUR_VALID_RAKUTEN_APP_ID_HERE" # ここをあなたのキーに置き換える！

# --- データベース初期化 ---
def init_db():
    # フォルダが存在しない場合は作成 (DBファイルとアップロード用)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # DB_NAMEをstr()で文字列に変換して接続
    conn = sqlite3.connect(str(DB_NAME)) 
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condiments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            expiry TEXT,
            image_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# アプリケーション初期化
app = FastAPI()
init_db()

# 静的ファイル設定 (CSS, JS, 画像)
app.mount("/static", StaticFiles(directory="static"), name="static")
# アップロードされた画像も外部からアクセスできるように設定
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

templates = Jinja2Templates(directory="templates")

# -----------------------------------------------------------
# API呼び出し関数 (レシピ検索の確実性を向上させるために修正済み)
# -----------------------------------------------------------
async def fetch_recipes_from_api(ingredients_query: str):
    """期限が近い調味料名 (ingredients_query) を使ってレシピAPIを呼び出す"""
    # 楽天APIはキーワードをスペースではなく '+' で結合することを推奨
    search_query = "+".join(ingredients_query.split())
    RAKUTEN_RECIPE_URL = "https://app.rakuten.co.jp/services/api/Recipe/RecipeSearch/20170426"

    async with httpx.AsyncClient() as client:
        try:
            print(f"DEBUG: 検索クエリ: {search_query}")

            response = await client.get(
                RAKUTEN_RECIPE_URL,
                params={
                    "applicationId": RAKUTEN_APP_ID,
                    "material": search_query, # 材料名での検索を使用
                    "format": "json"
                },
                timeout=10.0
            )
            
            print(f"DEBUG: Rakuten API Response Status: {response.status_code}")
            response.raise_for_status() 
            
            data = response.json()
            
            recipes = []
            recipe_list = data.get('recipes', [])

            for item in recipe_list:
                recipe = item.get('recipe', {})
                
                if recipe and recipe.get('recipeTitle'):
                    recipes.append({
                        "title": recipe.get('recipeTitle', 'タイトルなし'),
                        "url": recipe.get('recipeUrl', '#'),
                        "image": recipe.get('mediumImageUrl', recipe.get('largeImageUrl', ''))
                    })
            
            print(f"DEBUG: 抽出されたレシピ数: {len(recipes)}")
            return recipes
            
        except httpx.HTTPStatusError as e:
            error_text = f"HTTPエラーが発生しました: {e}. ステータスコード: {e.response.status_code}. レスポンス: {e.response.text[:100]}"
            print(f"🚨 楽天API呼び出し中にHTTPエラーが発生しました: {error_text}")
            return []
        except Exception as e:
            print(f"🚨 レシピAPI呼び出し中に予期せぬエラーが発生しました: {e}")
            return []

# -----------------------------------------------------------
# GET: 登録画面
# -----------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# -----------------------------------------------------------
# POST: 調味料の登録処理
# -----------------------------------------------------------
@app.post("/upload") 
async def register_condiment(
    name: str = Form(...),
    expiry: str = Form(None),
    image: UploadFile = File(None) 
):
    image_path = None
    
    if image and image.filename:
        extension = Path(image.filename).suffix
        unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}{extension}"
        file_path = UPLOAD_DIR / unique_filename
        
        try:
            with file_path.open("wb") as buffer:
                # ファイルストリームをコピー
                shutil.copyfileobj(image.file, buffer)
            
            image_path = f"/uploads/{unique_filename}"
            
        except Exception as e:
            print(f"ファイル保存エラー: {e}")
            raise HTTPException(status_code=500, detail="ファイルの保存に失敗しました。")

    # DBに保存
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    cur.execute(
        "INSERT INTO condiments (name, expiry, image_path) VALUES (?, ?, ?)",
        (name, expiry if expiry else None, image_path)
    )
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/list", status_code=303)

# -----------------------------------------------------------
# GET: HTML用 一覧表示
# -----------------------------------------------------------
@app.get("/list", response_class=HTMLResponse)
async def list_condiments(request: Request):
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    cur.execute("SELECT id, name, expiry, image_path FROM condiments ORDER BY created_at DESC")
    db_condiments = cur.fetchall()
    conn.close()
    
    condiments = []
    today = date.today()
    expiry_limit = today + timedelta(days=EXPIRY_THRESHOLD_DAYS)
    
    for row in db_condiments:
        item = {
            "id": row[0],
            "name": row[1],
            "expiry": row[2],
            "image_path": row[3],
            "is_expired": False,
            "near_expiry": False
        }
        
        if row[2]: # 期限日がある場合のみチェック
            try:
                expiry_date = datetime.strptime(row[2], "%Y-%m-%d").date()
                
                if expiry_date < today:
                    item["is_expired"] = True
                elif expiry_date <= expiry_limit:
                    item["near_expiry"] = True
            except ValueError:
                pass

        condiments.append(item)
    
    return templates.TemplateResponse("list.html", {"request": request, "condiments": condiments})

# -----------------------------------------------------------
# GET: API用 一覧表示 (JSON形式) 💡 新規追加
# -----------------------------------------------------------
@app.get("/api/list", response_class=JSONResponse)
async def api_list_condiments():
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    cur.execute("SELECT id, name, expiry, image_path FROM condiments ORDER BY created_at DESC")
    db_condiments = cur.fetchall()
    conn.close()
    
    condiments = []
    today = date.today()
    expiry_limit = today + timedelta(days=EXPIRY_THRESHOLD_DAYS)
    
    for row in db_condiments:
        item = {
            "id": row[0],
            "name": row[1],
            "expiry": row[2],
            "image_path": row[3],
            "is_expired": False,
            "near_expiry": False
        }
        
        if row[2]:
            try:
                expiry_date = datetime.strptime(row[2], "%Y-%m-%d").date()
                
                if expiry_date < today:
                    item["is_expired"] = True
                elif expiry_date <= expiry_limit:
                    item["near_expiry"] = True
            except ValueError:
                pass

        condiments.append(item)
    
    return JSONResponse(content=condiments)


# -----------------------------------------------------------
# POST: 削除処理
# -----------------------------------------------------------
@app.post("/delete/{item_id}")
async def delete_condiment(item_id: int):
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    # 削除前に画像パスを取得
    cur.execute("SELECT image_path FROM condiments WHERE id = ?", (item_id,))
    result = cur.fetchone()
    image_path_to_delete = result[0] if result else None
    
    # データベースからレコードを削除
    cur.execute("DELETE FROM condiments WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    # 画像ファイルがあれば削除
    if image_path_to_delete and image_path_to_delete.startswith("/uploads/"):
        filename = image_path_to_delete.split("/")[-1]
        file_to_delete = UPLOAD_DIR / filename
        if file_to_delete.exists():
            os.remove(file_to_delete)
            
    return RedirectResponse(url="/list", status_code=303)


# -----------------------------------------------------------
# GET: HTML用 期限間近の調味料を使ったレシピ検索ページ (ロジック修正済み)
# -----------------------------------------------------------
@app.get("/recipes", response_class=HTMLResponse)
async def get_near_expiry_recipes(request: Request):
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    expiry_limit = (date.today() + timedelta(days=EXPIRY_THRESHOLD_DAYS)).strftime("%Y-%m-%d")
    
    cur.execute("""
        SELECT name FROM condiments 
        WHERE expiry IS NOT NULL AND expiry != ''
        AND expiry <= ? 
        ORDER BY expiry ASC
    """, (expiry_limit,))
    
    near_expiry_items = [row[0] for row in cur.fetchall()]
    conn.close()

    # 検索クエリの決定
    query_display = " ".join(near_expiry_items)
    query_api = ""
    
    if not near_expiry_items:
        return templates.TemplateResponse("recipe_search.html", {
            "request": request,
            "recipes": [],
            "query": f"期限が{EXPIRY_THRESHOLD_DAYS}日以内に切れる調味料はありません。"
        })
    else:
        # 💡 修正: 複数の調味料がある場合、ランダムに一つ選んで検索クエリとする
        query_api = random.choice(near_expiry_items) 
    
    # APIを呼び出す
    recipes = await fetch_recipes_from_api(query_api)
    
    return templates.TemplateResponse("recipe_search.html", {
        "request": request,
        "recipes": recipes,
        "query": query_display, # 画面には結合された名前を表示
        "expiry_days": EXPIRY_THRESHOLD_DAYS
    })

# -----------------------------------------------------------
# GET: API用 期限間近レシピ検索 (JSON形式) 💡 新規追加
# -----------------------------------------------------------
@app.get("/api/recipes", response_class=JSONResponse)
async def api_get_near_expiry_recipes():
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    expiry_limit = (date.today() + timedelta(days=EXPIRY_THRESHOLD_DAYS)).strftime("%Y-%m-%d")
    
    cur.execute("""
        SELECT name FROM condiments 
        WHERE expiry IS NOT NULL AND expiry != ''
        AND expiry <= ? 
        ORDER BY expiry ASC
    """, (expiry_limit,))
    
    near_expiry_items = [row[0] for row in cur.fetchall()]
    conn.close()

    if not near_expiry_items:
        return JSONResponse(content={
            "query": f"期限が{EXPIRY_THRESHOLD_DAYS}日以内に切れる調味料はありません。",
            "recipes": []
        })

    # API検索に使うクエリは、ランダムに一つ選んだものにする
    query_display = " ".join(near_expiry_items)
    query_api = random.choice(near_expiry_items)
    
    recipes = await fetch_recipes_from_api(query_api)

    return JSONResponse(content={
        "query": query_display,
        "recipes": recipes
    })