import sqlite3
import shutil
import os
from datetime import date, timedelta
import httpx 
from fastapi import FastAPI, Request, File, UploadFile, Form, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from datetime import datetime
import random # 一意なファイル名生成のために追加

# --- 設定 ---
# ⚠️ 注意: Render環境では/tmp以下のデータは再起動やアイドル後に消去されます。
# 永続化が必要な場合は、Render DiskまたはAWS S3などの外部ストレージを使用してください。
TMP_DIR = Path(os.environ.get("TEMP_DIR", "/tmp/condiments_app")) 
DB_NAME = TMP_DIR / "condiments.db"
UPLOAD_DIR = TMP_DIR / "uploads"
# 期限切れが近いと見なす日数
EXPIRY_THRESHOLD_DAYS = 7 

# --- データベース初期化 ---
def init_db():
    # フォルダが存在しない場合は作成 (DBファイルとアップロード用)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    # DB_NAMEをstr()で文字列に変換して接続
    conn = sqlite3.connect(str(DB_NAME)) 
    cur = conn.cursor()
    
    # 既存のDBスキーマ (condiments.db) に合わせて created_at カラムを追加
    cur.execute("""
        CREATE TABLE IF NOT EXISTS condiments (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            expiry TEXT,
            image_path TEXT,
            created_at TEXT  
        )
    """)
    conn.commit()
    conn.close()

# アプリケーションの初期化とマウントの前にDB初期化（フォルダ作成）を実行
init_db() 


# FastAPIとテンプレート設定
app = FastAPI()

# テンプレート設定: 'templates' フォルダがプロジェクトルートにあると仮定
templates = Jinja2Templates(directory="templates")

# 静的ファイルの提供 (CSS, JS, noimage.pngなど): 'static' フォルダを公開
app.mount("/static", StaticFiles(directory="static"), name="static")

# アップロードファイルの提供: /tmp下のUPLOAD_DIRを公開
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# --- レシピAPI設定 ---
# RAKUTEN_APP_ID の値は環境変数から取得できない場合、デフォルト値が使われます
# ⚠️ 注意: このデフォルトIDはサンプルである可能性が高く、必ずご自身の有効なIDに置き換えるか、環境変数に設定してください。
RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "1068807561207277425")

# 🚨 修正: 最も基本的なAPIである RecipeSearch に戻す
RAKUTEN_RECIPE_URL = "https://app.rakuten.co.jp/services/api/Recipe/RecipeSearch/20170426"


# --- API呼び出し関数 ---
async def fetch_recipes_from_api(ingredients_query: str):
    """期限が近い調味料名 (ingredients_query) を使ってレシピAPIを呼び出す"""
    # 楽天APIはキーワードをスペースではなく '+' で結合することを推奨
    search_query = "+".join(ingredients_query.split())
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                RAKUTEN_RECIPE_URL,
                params={
                    "applicationId": RAKUTEN_APP_ID,
                    # RecipeSearch APIは 'keyword' をサポートしていない可能性もあるが、テストのためこのまま
                    "keyword": search_query, 
                    "format": "json"
                },
                timeout=10.0
            )
            response.raise_for_status() 
            data = response.json()
            
            # 🚨 デバッグ: APIが返した生データをログに出力する
            print(f"DEBUG: Rakuten API Response Data: {data}")
            
            recipes = []
            
            # APIの結果リストは 'recipes' キーの下にあると仮定
            recipe_list = []

            # RecipeSearch APIのレスポンス構造の確認
            if 'result' in data and 'recipes' in data['result']:
                recipe_list = data['result']['recipes']
            elif 'recipes' in data:
                recipe_list = data['recipes']
            elif 'result' in data and 'categoryRanking' in data['result']:
                 # CategoryRanking APIの典型的なレスポンス構造
                recipe_list = data['result']['categoryRanking']


            for item in recipe_list:
                # itemは通常、{'recipe': {...}} という構造
                recipe = item.get('recipe', {}) # レシピ情報があれば取得
                
                # 'categoryRanking' の場合は、item自体がレシピ情報である可能性もある
                if not recipe and 'recipeTitle' in item:
                    recipe = item 

                # 有効なレシピ情報が見つかった場合のみ追加
                if recipe:
                    recipes.append({
                        "title": recipe.get('recipeTitle', 'タイトルなし'),
                        "url": recipe.get('recipeUrl', '#'),
                        "image": recipe.get('mediumImageUrl', '')
                    })
            return recipes
            
        except httpx.HTTPStatusError as e:
            # APIキー無効 (403), 検索失敗 (400) など
            print(f"HTTPエラーが発生しました: {e}. レスポンス: {response.text[:100] if 'response' in locals() else 'N/A'}")
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
@app.post("/upload") 
async def register_condiment(
    name: str = Form(...),
    expiry: str = Form(None),
    # 修正: index.html側のname属性に合わせて 'image' ではなく 'file' にする
    image: UploadFile = File(None)
):
    image_path = None
    
    # 現在時刻を取得
    current_time = datetime.now().isoformat()
    
    if image and image.filename:
        # ファイル名の生成
        ext = Path(image.filename).suffix
        # ファイル名にランダムな4桁の数字を追加し、重複を避ける
        unique_filename = f"{Path(name).stem}_{date.today().strftime('%Y%m%d')}_{random.randint(1000, 9999)}{ext}"
        file_path = UPLOAD_DIR / unique_filename
        
        try:
            # ファイルの保存
            with file_path.open("wb") as buffer:
                # ファイルポインタを先頭に戻してから書き込み
                image.file.seek(0)
                shutil.copyfileobj(image.file, buffer)
                
            # DBに保存するパスは、ウェブからアクセス可能な StaticFiles のパス形式にする
            image_path = f"/uploads/{unique_filename}" 
        except Exception as e:
            print(f"ファイル保存エラー: {e}")
            # サーバーログでエラーを確認できるようにする
            raise HTTPException(status_code=500, detail=f"ファイルのアップロードに失敗しました: {e}")

    # DBに保存 (DB_NAMEをstr()で変換)
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    # created_at も同時に挿入
    cur.execute(
        "INSERT INTO condiments (name, expiry, image_path, created_at) VALUES (?, ?, ?, ?)",
        (name, expiry if expiry else None, image_path, current_time)
    )
    conn.commit()
    conn.close()

    return RedirectResponse(url="/list", status_code=303)


# GET: 調味料一覧表示
@app.get("/list", response_class=HTMLResponse)
async def list_condiments(request: Request):
    # DB接続 (DB_NAMEをstr()で変換)
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
    # DB接続 (DB_NAMEをstr()で変換)
    conn = sqlite3.connect(str(DB_NAME))
    cur = conn.cursor()
    
    # 削除対象の画像パスを取得
    cur.execute("SELECT image_path FROM condiments WHERE id = ?", (item_id,))
    row = cur.fetchone()
    
    # DBから削除
    cur.execute("DELETE FROM condiments WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    
    # 物理ファイルを削除 (DB削除後に実行)
    if row and row[0]:
        # image_path は /uploads/ファイル名 形式なので、ファイル名だけを取得
        image_filename = Path(row[0]).name
        file_to_delete = UPLOAD_DIR / image_filename
        
        if file_to_delete.exists():
            os.remove(file_to_delete)
            
    return RedirectResponse(url="/list", status_code=303)


# -----------------------------------------------------------
# GET: 期限間近の調味料を使ったレシピ検索ページ
# -----------------------------------------------------------
@app.get("/recipes", response_class=HTMLResponse)
async def get_near_expiry_recipes(request: Request):
    # DB接続 (DB_NAMEをstr()で変換)
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

    # 調味料名をクエリとして結合
    query = " ".join(near_expiry_items) 
    
    # APIを呼び出す
    recipes = await fetch_recipes_from_api(query) 

    return templates.TemplateResponse("recipe_search.html", {
        "request": request,
        "recipes": recipes, 
        "query": query,
    })