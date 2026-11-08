# -*- coding: utf-8 -*-
"""
MOMO 購物網價格查詢與視覺化 (Streamlit)
執行方式: streamlit run momo_app.py
"""
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
from curl_cffi import requests

# ------------------------------------------------------------------
# 基本設定
# ------------------------------------------------------------------
st.set_page_config(page_title="MOMO 價格查詢", page_icon="🛒", layout="wide")

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HOME_URL = "https://www.momoshop.com.tw/"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ------------------------------------------------------------------
# 爬蟲 (CODE01)
# ------------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(ttl=600, show_spinner=False)
def fetch_products(keyword: str, pages: int, delay: float = 1.0) -> pd.DataFrame:
    """爬取指定關鍵字的前 N 頁商品，回傳 DataFrame。"""
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(HOME_URL, headers=HEADERS, timeout=20)  # 先取得 cookie

    rows = []
    for page in range(1, pages + 1):
        resp = session.post(
            API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=20
        )
        if resp.status_code != 200:
            raise RuntimeError(f"第 {page} 頁請求失敗 (HTTP {resp.status_code})：{resp.text[:200]}")

        goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not goods:  # 沒有更多結果就停止
            break

        for p in goods:
            code = p.get("goodsCode", "")
            rows.append(
                {
                    "頁數": page,
                    "品名": p.get("goodsName", ""),
                    "價格": p.get("goodsPrice", ""),
                    "連結": f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={code}"
                    if code
                    else "",
                    "_code": code,
                }
            )
        time.sleep(delay)  # 禮貌性延遲，避免被封鎖

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # 價格清理 (CODE01 後半段)：移除 $ , 與所有非數字字元
    df["價格"] = df["價格"].astype(str).str.replace(r"[^\d]", "", regex=True)
    df["價格"] = pd.to_numeric(df["價格"], errors="coerce")
    df = df.dropna(subset=["價格"])
    df["價格"] = df["價格"].astype(int)

    # 去除重複商品
    if df["_code"].astype(bool).any():
        df = df.drop_duplicates(subset="_code")
    df = df.drop(columns="_code").reset_index(drop=True)
    df.index.name = "編號"
    return df


# ------------------------------------------------------------------
# 圖表 (CODE02)
# 使用 Plotly：由瀏覽器渲染文字，不需要另外安裝中文字型，也不用 !wget
# ------------------------------------------------------------------
def short_name(s: str, n: int = 14) -> str:
    return s if len(s) <= n else s[:n] + "…"


def add_price_range(df: pd.DataFrame, bins: int = 5) -> pd.DataFrame:
    """依價格切成區間，供圓餅圖/旭日圖使用。"""
    out = df.copy()
    lo, hi = out["價格"].min(), out["價格"].max()
    if lo == hi:
        out["價格區間"] = f"${lo:,}"
        return out
    edges = pd.interval_range(start=lo, end=hi, periods=bins)
    cut = pd.cut(out["價格"], bins=[lo - 1] + [int(e.right) for e in edges])
    labels = {}
    for iv in cut.cat.categories:
        labels[iv] = f"${int(iv.left) + 1:,} – ${int(iv.right):,}"
    out["價格區間"] = cut.map(labels).astype(str)
    return out


def line_chart(df: pd.DataFrame, keyword: str):
    mean_price = df["價格"].mean()
    fig = px.line(
        df.reset_index(),
        x="編號",
        y="價格",
        markers=True,
        hover_data={"品名": True, "價格": ":,", "編號": False},
        title=f"MOMO 電商網站上「{keyword}」售價",
    )
    fig.add_hline(
        y=mean_price,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均 ${mean_price:,.0f}",
        annotation_position="top left",
    )
    fig.update_layout(xaxis_title="商品編號 (store id)", yaxis_title="價格 (price)", height=520)
    return fig


def pie_chart(df: pd.DataFrame, keyword: str):
    d = add_price_range(df)
    cnt = d.groupby("價格區間").size().reset_index(name="商品數")
    # 依區間下限排序，圖例才不會亂
    cnt["_k"] = cnt["價格區間"].str.extract(r"\$([\d,]+)")[0].str.replace(",", "").astype(int)
    cnt = cnt.sort_values("_k")
    fig = px.pie(
        cnt,
        names="價格區間",
        values="商品數",
        hole=0.35,
        title=f"「{keyword}」價格區間分布",
        category_orders={"價格區間": cnt["價格區間"].tolist()},
    )
    fig.update_traces(textinfo="percent+label+value")
    fig.update_layout(height=560)
    return fig


def sunburst_chart(df: pd.DataFrame, keyword: str, top_n: int):
    d = add_price_range(df)
    # 每個區間取價格最高的前 top_n 筆，避免圖面過於擁擠
    d = (
        d.sort_values("價格", ascending=False)
        .groupby("價格區間", group_keys=False)
        .head(top_n)
        .reset_index()
    )
    d["商品"] = d["品名"].map(short_name) + " #" + d["編號"].astype(str)  # 加編號避免重名
    d["根"] = keyword
    fig = px.sunburst(
        d,
        path=["根", "價格區間", "商品"],
        values="價格",
        color="價格",
        color_continuous_scale="Viridis",
        title=f"「{keyword}」價格區間 → 商品 旭日圖 (每區間取前 {top_n} 高價)",
    )
    fig.update_layout(height=700)
    return fig


# ------------------------------------------------------------------
# 介面
# ------------------------------------------------------------------
st.title("🛒 MOMO 商品價格查詢與視覺化")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    delay = st.slider("每頁延遲秒數", 0.5, 3.0, 1.0, 0.5, help="延遲越長越不容易被擋")
    chart_type = st.radio("圖表類型", ["折線圖", "圓餅圖", "旭日圖"])
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
        st.stop()
    try:
        with st.spinner(f"正在爬取「{keyword}」前 {pages} 頁…"):
            result = fetch_products(keyword.strip(), int(pages), float(delay))
        st.session_state["result"] = result
        st.session_state["keyword"] = keyword.strip()
    except Exception as e:
        st.error(f"爬取失敗：{e}")
        st.stop()

if "result" not in st.session_state:
    st.info("請在左側輸入商品與頁數，然後按「開始查詢」。")
    st.stop()

df: pd.DataFrame = st.session_state["result"]
kw: str = st.session_state["keyword"]

if df.empty:
    st.warning("沒有找到商品，可能是關鍵字無結果或被網站擋下。")
    st.stop()

# 價格篩選
pmin, pmax = int(df["價格"].min()), int(df["價格"].max())
if pmin < pmax:
    sel = st.slider("價格範圍篩選", pmin, pmax, (pmin, pmax))
    view = df[df["價格"].between(*sel)]
else:
    view = df
if view.empty:
    st.warning("此價格範圍內沒有商品。")
    st.stop()

# 統計
c1, c2, c3, c4 = st.columns(4)
c1.metric("商品數", f"{len(view):,}")
c2.metric("平均價格", f"${view['價格'].mean():,.0f}")
c3.metric("最高價格", f"${view['價格'].max():,}")
c4.metric("最低價格", f"${view['價格'].min():,}")

# 圖表
if chart_type == "折線圖":
    st.plotly_chart(line_chart(view, kw), use_container_width=True)
elif chart_type == "圓餅圖":
    st.plotly_chart(pie_chart(view, kw), use_container_width=True)
else:
    top_n = st.slider("每個價格區間顯示商品數", 3, 20, 8)
    st.plotly_chart(sunburst_chart(view, kw, top_n), use_container_width=True)

# 資料表與下載
st.subheader("商品資料")
st.dataframe(
    view,
    use_container_width=True,
    column_config={"連結": st.column_config.LinkColumn("連結", display_text="前往商品")},
)
csv = view.to_csv(encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    "⬇️ 下載 CSV",
    data=csv,
    file_name=f"MOMO_{kw}_{datetime.now():%Y%m%d}.csv",
    mime="text/csv",
)
