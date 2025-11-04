import sqlite3
import shutil
import os
from datetime import date, timedelta
import httpx # API呼び出し用
from fastapi import FastAPI, Request, File, UploadFile, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path

# --- 設定 ---
# 💡 デプロイ環境で書き込み可能な/tmp以下のパスを指定
TMP_DIR = Path(os.environ.get("TEMP_DIR", "/tmp/condiments_app")) 
DB_NAME = TMP_DIR / "condiments.db"
UPLOAD_DIR = TMP_DIR / "uploads"
# 期限切れが近いと見なす日数
EXPIRY_THRESHOLD_DAYS = 7 

# --- データベース初期化 ---
def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # 🚨 修正1: DB_NAMEをstr()で文字列に変換して渡す
    conn = sqlite3.connect(str(DB_NAME)) 
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condiments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            expiry TEXT,
            image_path TEXT
        )
    """)
    conn.commit()
    conn.close()

# アプリケーションの初期化とマウントの前にDB初期化（フォルダ作成）を実行
init_db() 


# FastAPIとテンプレート設定
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 静的ファイルの提供 (CSS, JS, 画像など)
app.mount("/static", StaticFiles(directory="static"), name="static")

# StaticFiles.__init__() から 'name' 引数を削除
# フォルダが init_db() で作成されているため、ここでマウント可能
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# --- レシピAPI設定 (キーワード検索APIに切り替え) ---
RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "1013897941253771301") 
RAKUTEN_RECIPE_URL = "https://app.rakuten.co.jp/services/api/Recipe/RecipeSearch/20170426" 

# --- API呼び出し関数 ---
async def fetch_recipes_from_api(ingredients_query: str):
    """
    期限が近い調味料名 (ingredients_query) を使ってレシピAPIを呼び出す
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                RAKUTEN_RECIPE_URL,
                params={
                    "applicationId": RAKUTEN_APP_ID,
                    "keyword": ingredients_query, 
                    "format": "json"
                },
                timeout=10.0
            )
            response.raise_for_status() 
            data = response.json()
            
            recipes = []
            if 'result' in data and 'recipes' in data['result']:
                for item in data['result']['recipes']:
                    recipe = item['recipe']
                    recipes.append({
                        "title": recipe.get('recipeTitle', 'タイトルなし'),
                        "url": recipe.get('recipeUrl', '#')
                    })
            return recipes
            
        except httpx.HTTPStatusError as e:
            print(f"HTTPエラーが発生しました: {e}")
            return []
        except Exception as e:
            print(f"レシピAPI呼び出し中にエラーが発生しました: {e}")
            return []


# --- エンドポイント ---

# GET: 登録フォーム表示
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# POST: 調味料の登録処理
# 🚨 修正: エンドポイントを /register から /upload に変更
@app.post("/upload") 
async def register_condiment(
    name: str = Form(...),
    expiry: str = Form(None),
    image: UploadFile = File(None)
):
    image_path = None
    if image and image.filename:
        ext = Path(image.filename).suffix
        unique_filename = f"{Path(name).stem}_{date.today().strftime('%Y%m%d')}_{os.urandom(8).hex()}{ext}"
        file_path = UPLOAD_DIR / unique_filename
        
        try:
            with file_path.open("wb") as buffer:
                # 登録処理でファイルポインタをリセットする処理を追加
                image.file.seek(0)
                shutil.copyfileobj(image.file, buffer)
                
            # DBに保存するパスは、StaticFilesのパス形式（/uploads/ファイル名）にする
            image_path = f"/uploads/{unique_filename}" 
        except Exception as e:
            print(f"ファイル保存エラー: {e}")
            raise HTTPException(status_code=500, detail="ファイルのアップロードに失敗しました。")

    # DBに保存
    # 🚨 修正2: DB_NAMEをstr()で文字列に変換して渡す
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO condiments (name, expiry, image_path) VALUES (?, ?, ?)",
        (name, expiry if expiry else None, image_path)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(url="/list", status_code=303)


# GET: 調味料一覧表示
@app.get("/list", response_class=HTMLResponse)
async def list_condiments(request: Request):
    # 🚨 修正3: DB_NAMEをstr()で文字列に変換して渡す
    conn = sqlite3.connect(str(DB_NAME))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # 全ての調味料を期限が近い順に取得
    cur.execute("""
        SELECT id, name, expiry, image_path 
        FROM condiments 
        ORDER BY CASE WHEN expiry IS NULL THEN 1 ELSE 0 END, expiry ASC
    """)
    condiments = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    # 期限切れチェック
    today = date.today()
    for item in condiments:
        item['is_expired'] = False
        item['near_expiry'] = False
        if item['expiry']:
            try:
                expiry_date = date.fromisoformat(item['expiry'])
                days_left = (expiry_date - today).days
                if days_left <= 0:
                    item['is_expired'] = True
                elif days_left <= EXPIRY_THRESHOLD_DAYS:
                    item['near_expiry'] = True
            except ValueError:
                pass

    return templates.TemplateResponse("list.html", {"request": request, "condiments": condiments})


# POST: 調味料の削除
@app.post("/delete/{item_id}")
async def delete_condiment(item_id: int):
    # 🚨 修正4: DB_NAMEをstr()で文字列に変換して渡す
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    # 削除対象の画像パスを取得
    cur.execute("SELECT image_path FROM condiments WHERE id = ?", (item_id,))
    row = cur.fetchone()
    if row and row[0]:
        # image_path は /uploads/ファイル名 形式なので、ファイル名だけを取得
        image_filename = Path(row[0]).name
        file_to_delete = UPLOAD_DIR / image_filename
        
        if file_to_delete.exists():
            os.remove(file_to_delete)
            
    # DBから削除
    cur.execute("DELETE FROM condiments WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/list", status_code=303)


# -----------------------------------------------------------
# GET: 期限間近の調味料を使ったレシピ検索ページ
# -----------------------------------------------------------
@app.get("/recipes", response_class=HTMLResponse)
async def get_near_expiry_recipes(request: Request):
    # 🚨 修正5: DB_NAMEをstr()で文字列に変換して渡す
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    # 期限が今日から設定日数以内のアイテムを抽出
    expiry_limit = (date.today() + timedelta(days=EXPIRY_THRESHOLD_DAYS)).strftime("%Y-%m-%d")
    
    cur.execute("""
        SELECT name FROM condiments 
        WHERE expiry IS NOT NULL AND expiry != ''
        AND expiry <= ? 
        ORDER BY expiry ASC
    """, (expiry_limit,))
    
    # 取得した調味料名をリスト化
    near_expiry_items = [row[0] for row in cur.fetchall()]
    conn.close()

    # 期限が近い調味料がない場合の処理
    if not near_expiry_items:
        return templates.TemplateResponse("recipe_search.html", {
            "request": request,
            "recipes": [],
            "query": f"期限が{EXPIRY_THRESHOLD_DAYS}日以内に切れる調味料はありません。",
        })

    # 調味料名をクエリとして結合 (例: "しょうゆ みりん")
    query = " ".join(near_expiry_items) 
    
    # APIを呼び出す
    recipes = await fetch_recipes_from_api(query) 

    return templates.TemplateResponse("recipe_search.html", {
        "request": request,
        "recipes": recipes, 
        "query": query,
    })