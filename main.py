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

# import random # 複数のレシピを検索するため、ランダム選択は不要になる



# --- 設定 ---

# 💡 ローカル実行用にデータ保存先をプロジェクトフォルダ内の 'app_data' に設定

APP_DATA_DIR = Path("./app_data")

DB_NAME = APP_DATA_DIR / "condiments.db"

UPLOAD_DIR = APP_DATA_DIR / "uploads"

# 期限切れが近いと見なす日数

EXPIRY_THRESHOLD_DAYS = 7



# --- レシピAPI設定 ---

# 1. Google Cloud Consoleで取得したAPIキー

GOOGLE_API_KEY = "AIzaSyBw0E7pet5a9zonymLCXs2stcrGkiJbrZo"

# 2. カスタム検索エンジンで取得したCSE ID

GOOGLE_CSE_ID = "54d53a5e4d8e94217"



# --- データベース初期化 ---

def init_db():

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # templatesディレクトリがない場合は作成（Jinja2Templatesの参照先に必要）

    os.makedirs("templates", exist_ok=True)

   

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

# テンプレートファイルは 'templates' ディレクトリにある前提で修正

app.mount("/static", StaticFiles(directory="static"), name="static")

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")



templates = Jinja2Templates(directory="templates") # テンプレートディレクトリは "templates"



# -----------------------------------------------------------

# API呼び出し関数 (Google Custom Search JSON APIを使用)

# -----------------------------------------------------------

async def fetch_recipes_from_api(ingredients_query: str):

    """

    調味料名を使ってGoogle Custom Search APIを呼び出し、レシピを検索する。

    """

   

    # 実際にはAPIキーの検証ロジックをここに記述

    # if GOOGLE_API_KEY == "YOUR_GOOGLE_API_KEY" or GOOGLE_CSE_ID == "YOUR_CSE_ID":

    #     print("🚨 エラー: APIキーが設定されていません。")

    #     return []



    GOOGLE_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

    search_query = f"{ingredients_query} レシピ"

    # print(f"DEBUG: Google Search クエリ: {search_query}")



    async with httpx.AsyncClient() as client:

        try:

            response = await client.get(

                GOOGLE_SEARCH_URL,

                params={

                    "key": GOOGLE_API_KEY,      

                    "cx": GOOGLE_CSE_ID,      

                    "q": search_query,        

                    "num": 5                  

                },

                timeout=10.0

            )

           

            # print(f"DEBUG: Google API Response Status: {response.status_code}")

            response.raise_for_status()

           

            data = response.json()

            recipe_list = data.get('items', [])

           

            recipes = []

            for item in recipe_list:

                # 検索結果からタイトルとURLを抽出

                recipes.append({

                    "title": item.get('title', 'タイトルなし'),

                    "url": item.get('link', '#'),

                    # 画像は取得が複雑なため、ここでは省略

                    "image": "/static/recipe.png" # 存在する静的ファイルを参照するように修正

                })

           

            # print(f"DEBUG: 抽出されたレシピ数: {len(recipes)}")

            return recipes

           

        except httpx.HTTPStatusError as e:

            error_text = f"HTTPエラーが発生しました: {e.response.status_code}. レスポンス: {e.response.text[:100]}"

            print(f"🚨 Google API呼び出し中にHTTPエラーが発生しました: {error_text}")

            return []

        except Exception as e:

            print(f"🚨 レシピAPI呼び出し中に予期せぬエラーが発生しました: {e}")

            return []



# -----------------------------------------------------------

# GET: 登録画面 (/templates/index.html を参照)

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

        unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}{extension}"

        file_path = UPLOAD_DIR / unique_filename

       

        try:

            with file_path.open("wb") as buffer:

                # ファイルポインタを先頭に戻す必要があるため、seek(0)を追加

                image.file.seek(0)

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

# GET: HTML用 一覧表示 (/templates/list.html を参照)

# -----------------------------------------------------------

@app.get("/list", response_class=HTMLResponse)

async def list_condiments(request: Request):

    conn = sqlite3.connect(str(DB_NAME))

    cur = conn.cursor()

    # 💡 修正: created_at (登録日) も取得するように変更

    cur.execute("SELECT id, name, expiry, image_path, created_at FROM condiments ORDER BY created_at DESC")

    db_condiments = cur.fetchall()

    conn.close()

   

    condiments = []

    today = date.today()

    expiry_limit = today + timedelta(days=EXPIRY_THRESHOLD_DAYS)

   

    for row in db_condiments:

        # DBからの結果を辞書として扱う

        item = {

            "id": row[0],

            "name": row[1],

            "expiry": row[2],

            "image_path": row[3],

            "created_at": row[4], # DBから取得した日時

            "is_expired": False,

            "near_expiry": False

        }

       

        # list.htmlのデータ属性用に、登録日をISO形式で追加

        item["registered_date"] = item["created_at"].split(' ')[0]

       

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

   

    return templates.TemplateResponse("list.html", {"request": request, "condiments": condiments})



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

# GET: HTML用 期限間近の調味料を使ったレシピ検索ページ (修正)

# -----------------------------------------------------------

@app.get("/recipes", response_class=HTMLResponse)

async def get_near_expiry_recipes(request: Request):

    conn = sqlite3.connect(str(DB_NAME))

    cur = conn.cursor()

   

    today = date.today().strftime("%Y-%m-%d")

    expiry_limit = (date.today() + timedelta(days=EXPIRY_THRESHOLD_DAYS)).strftime("%Y-%m-%d")

   

    # 期限切れではない、かつ期限が近いアイテムを取得

    cur.execute("""

        SELECT name FROM condiments

        WHERE expiry IS NOT NULL AND expiry != ''

        AND expiry >= ? AND expiry <= ?

        ORDER BY expiry ASC

    """, (today, expiry_limit))

   

    near_expiry_items = [row[0] for row in cur.fetchall()]

    conn.close()



    # 💡 ノイズ除去リスト

    IGNORE_KEYWORDS = ["無添加", "特選", "大容量", "減塩", "プレミアム", "限定", "だし", "つゆ", "ソース", "ドレッシング", "たれ", "タレ"]

   

    # 調味料リストからノイズを除去し、主要なキーワードのみを抽出

    cleaned_items = []

    for item_name in near_expiry_items:

        clean_name = item_name

        for noise in IGNORE_KEYWORDS:

            clean_name = clean_name.replace(noise, "").strip()

       

        if clean_name:

            clean_name = " ".join(clean_name.split())

            cleaned_items.append(clean_name)

   

    # 表示用のクエリ（カンマ区切りで表示）

    query_display = ", ".join(near_expiry_items)

   

    # --- 修正: 複数の調味料で個別に検索し、結果を統合する ---

    all_recipes = []

   

    if cleaned_items:

        for ingredient_query in set(cleaned_items): # 重複検索を避けるためsetを使用

            # 外部APIでレシピを検索 (非同期呼び出し)

            current_recipes = await fetch_recipes_from_api(ingredient_query)

           

            # レシピにどの調味料で検索したか('used_ingredient')の情報も追加

            for recipe in current_recipes:

                # 検索クエリ（クリーニングされた名前）を付加

                recipe['used_ingredient'] = ingredient_query

                all_recipes.append(recipe)

    # -----------------------------------------------------------



    if not all_recipes:

        message = f"期限が{EXPIRY_THRESHOLD_DAYS}日以内に切れる調味料はありません。または、レシピが見つかりませんでした。"

        return templates.TemplateResponse("recipe_search.html", {

            "request": request,

            "recipes": [],

            "query_ingredients": message # テンプレートで参照するように修正

        })



    # テンプレートに渡す

    return templates.TemplateResponse("recipe_search.html", {

        "request": request,

        "recipes": all_recipes, # 統合されたレシピリスト

        "query_ingredients": query_display, # 画面には元の名前をすべて表示

        "expiry_days": EXPIRY_THRESHOLD_DAYS

    })



# -----------------------------------------------------------

# GET: API用 期限間近レシピ検索 (JSON形式) (修正)

# -----------------------------------------------------------

@app.get("/api/recipes", response_class=JSONResponse)

async def api_get_near_expiry_recipes():

    # ... (DB接続とnear_expiry_itemsの取得は /recipes と同様) ...

    conn = sqlite3.connect(str(DB_NAME))

    cur = conn.cursor()

    today = date.today().strftime("%Y-%m-%d")

    expiry_limit = (date.today() + timedelta(days=EXPIRY_THRESHOLD_DAYS)).strftime("%Y-%m-%d")

   

    cur.execute("""

        SELECT name FROM condiments

        WHERE expiry IS NOT NULL AND expiry != ''

        AND expiry >= ? AND expiry <= ?

        ORDER BY expiry ASC

    """, (today, expiry_limit))

   

    near_expiry_items = [row[0] for row in cur.fetchall()]

    conn.close()



    # 💡 ノイズ除去リスト

    IGNORE_KEYWORDS = ["無添加", "特選", "大容量", "減塩", "プレミアム", "限定", "だし", "つゆ", "ソース", "ドレッシング", "たれ", "タレ"]

   

    # 調味料リストからノイズを除去し、主要なキーワードのみを抽出

    cleaned_items = []

    for item_name in near_expiry_items:

        clean_name = item_name

        for noise in IGNORE_KEYWORDS:

            clean_name = clean_name.replace(noise, "").strip()

       

        if clean_name:

            clean_name = " ".join(clean_name.split())

            cleaned_items.append(clean_name)

   

    query_display = ", ".join(near_expiry_items)



    # --- 修正: 複数の調味料で個別に検索し、結果を統合する ---

    all_recipes = []

   

    if cleaned_items:

        for ingredient_query in set(cleaned_items):

            current_recipes = await fetch_recipes_from_api(ingredient_query)

           

            for recipe in current_recipes:

                recipe['used_ingredient'] = ingredient_query

                all_recipes.append(recipe)

    # -----------------------------------------------------------



    if not all_recipes:

        return JSONResponse(content={

            "query": f"期限が{EXPIRY_THRESHOLD_DAYS}日以内に切れる調味料はありません。",

            "recipes": []

        })



    return JSONResponse(content={

        "query": query_display,

        "recipes": all_recipes # 統合されたレシピリストを返す

    })