"""
このファイルは、最初の画面読み込み時にのみ実行される初期化処理が記述されたファイルです。
"""

############################################################
# ライブラリの読み込み
############################################################
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from uuid import uuid4
import sys
import unicodedata
from dotenv import load_dotenv
import streamlit as st
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
import utils
import constants as ct
import csv
import openai

############################################################
# 設定関連
############################################################
load_dotenv()


############################################################
# 関数定義
############################################################

def initialize():
    """
    画面読み込み時に実行する初期化処理
    """
    # 初期化データの用意
    initialize_session_state()
    # ログ出力用にセッションIDを生成
    initialize_session_id()
    # ログ出力の設定
    initialize_logger()
    # csvファイルに在庫データ列を追加
    initialize_stock_status()
    # RAGのRetrieverを作成
    initialize_retriever()



def initialize_logger():
    """
    ログ出力の設定
    """
    os.makedirs(ct.LOG_DIR_PATH, exist_ok=True)
    
    logger = logging.getLogger(ct.LOGGER_NAME)

    if logger.hasHandlers():
        return

    log_handler = TimedRotatingFileHandler(
        os.path.join(ct.LOG_DIR_PATH, ct.LOG_FILE),
        when="D",
        encoding="utf8"
    )
    formatter = logging.Formatter(
        f"[%(levelname)s] %(asctime)s line %(lineno)s, in %(funcName)s, session_id={st.session_state.session_id}: %(message)s"
    )
    log_handler.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)


def initialize_session_id():
    """
    セッションIDの作成
    """
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid4().hex


def initialize_session_state():
    """
    初期化データの用意
    """
    if "messages" not in st.session_state:
        st.session_state.messages = []


def initialize_retriever():
    """
    Retrieverを作成
    """
    logger = logging.getLogger(ct.LOGGER_NAME)

    if "retriever" in st.session_state:
        return
    
    loader = CSVLoader(ct.RAG_SOURCE_PATH, encoding="utf-8")
    docs = loader.load()

    # OSがWindowsの場合、Unicode正規化と、cp932（Windows用の文字コード）で表現できない文字を除去
    for doc in docs:
        doc.page_content = adjust_string(doc.page_content)
        for key in doc.metadata:
            doc.metadata[key] = adjust_string(doc.metadata[key])

    docs_all = []
    for doc in docs:
        docs_all.append(doc.page_content)

    embeddings = OpenAIEmbeddings()
    db = Chroma.from_documents(docs, embedding=embeddings)

    retriever = db.as_retriever(search_kwargs={"k": ct.TOP_K})

    bm25_retriever = BM25Retriever.from_texts(
        docs_all,
        preprocess_func=utils.preprocess_func,
        k=ct.TOP_K
    )
    ensemble_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, retriever],
        weights=ct.RETRIEVER_WEIGHTS
    )

    st.session_state.retriever = ensemble_retriever


def adjust_string(s):
    """
    Windows環境でRAGが正常動作するよう調整
    
    Args:
        s: 調整を行う文字列
    
    Returns:
        調整を行った文字列
    """
    # 調整対象は文字列のみ
    if type(s) is not str:
        return s

    # OSがWindowsの場合、Unicode正規化と、cp932（Windows用の文字コード）で表現できない文字を除去
    if sys.platform.startswith("win"):
        s = unicodedata.normalize('NFC', s)
        s = s.encode("cp932", "ignore").decode("cp932")
        return s
    
    # OSがWindows以外の場合はそのまま返す
    return s

# 在庫ステータスを追加（生成AIを使用）
def initialize_stock_status():
    """
    CSVファイルにstock_status列を追加し、生成AIを使用して在庫ステータスを割り振る。
    すでにstock_status列が存在する場合は処理をスキップする。
    """
    with open(ct.RAG_SOURCE_PATH, mode='r', encoding='utf-8') as infile:
        reader = csv.DictReader(infile)
        
        # すでにstock_status列が存在する場合は処理をスキップ
        if not reader.fieldnames or "stock_status" in reader.fieldnames:
            return
        
        # 新しい列を追加
        fieldnames = reader.fieldnames + ["stock_status"]
        rows = []

        # 各行に生成AIを使用して在庫ステータスを割り振る
        for row in reader:
            product_name = row["name"]  # 商品名を基に在庫ステータスを生成
            stock_status = generate_stock_status(product_name)  # 生成AIで在庫ステータスを生成
            row["stock_status"] = stock_status
            rows.append(row)
    
    # ファイルを上書きして保存
    with open(ct.RAG_SOURCE_PATH, mode='w', encoding='utf-8', newline='') as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_stock_status(product_name):
    """
    生成AIを使用して在庫ステータスを生成する。

    Args:
        product_name (str): 商品名

    Returns:
        str: 生成された在庫ステータス("あり", "残りわずか", "なし"）
    """
    prompt = (
        f"以下の商品に対して適切な在庫ステータスを生成してください。\n\n"
        f"商品名: {product_name}\n\n"
        "在庫ステータスは次のいずれかから選んでください: 'あり', '残りわずか', 'なし'。\n"
        "回答は必ず1つの選択肢のみを返してください。"
    )

    # 在庫ステータスを生成
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "あなたは在庫ステータスを生成するアシスタントです。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5,
        max_tokens=20
    )
    stock_status = response.choices[0].message["content"].strip()
    
    # 応答が期待される値でない場合のデフォルト処理
    if stock_status not in ["あり", "残りわずか", "なし"]:
        stock_status = "なし"  # デフォルト値
    
    return stock_status