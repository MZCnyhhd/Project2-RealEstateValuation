"""北京二手房行情基准层（渠道1：政府公开数据）。

数据优先级（每个数据块独立标注来源，绝不拿演示数据冒充官方）：
1. data_beijing/ 下的规范化 CSV（由 refresh_from_api() 从开放平台自动拉取生成）：
   - price_bands.csv     列: price_band,low,high,deals,month  （按价格存量房网签月统计）
   - city_inventory.csv  列: as_of_date,sets_wan,area_wan_sqm （全市可售存量房，来自各区网签统计文件）
   - district_monthly.csv 列: district,deals,month            （各区月度网签——官方暂未开放分区数据，通常不存在）
2. 内置 SEED 演示数据（仅价格段有；区域明细无数据时卡片显示"待接入"）

API：userApply.jsp?id=<文件编号>&key=<BJ_DATA_USERKEY> 返回 JSON{address}，
再 GET address 得 CSV。注意：本机 Python OpenSSL 与该站 TLS 不兼容（BAD_ECPOINT），
故用 curl 子进程实现（Windows/Render 均自带 curl）。

合规约定：官方数据展示必须标注来源「北京市公共数据开放平台（市住建委）」；
本模块只处理统计汇总数字，不含任何个人信息。
"""

import csv
import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_beijing")
_RAW_DIR = os.path.join(_DATA_DIR, "raw")
API_BASE = "https://data.beijing.gov.cn/cms/web/bjdata/api/userApply.jsp"

# 开放平台文件编号（csv 格式）
FILE_IDS = {
    "price": "9bd79612261d4075b9a2562927077f3096489",     # 按价格存量房网上签约月统计
    "district": "6fdd2a59f8d04476a00fd40c1ed1b46c35969",  # 各区存量房网上签约统计（实为全市日度）
}

SOURCE_OFFICIAL = "official"
SOURCE_DEMO = "demo"

_DISTRICTS = ["东城", "西城", "朝阳", "海淀", "丰台", "石景山", "门头沟", "房山",
              "通州", "顺义", "昌平", "大兴", "怀柔", "平谷", "密云", "延庆", "经开区"]

# ---- SEED 演示数据（仅当官方 price_bands.csv 不存在时使用，卡片会标注"演示样例"） ----
SEED_PRICE_BANDS = [
    {"band": "200万以下", "low": 0, "high": 200, "deals": 5800, "month": "演示样例"},
    {"band": "200-300万", "low": 200, "high": 300, "deals": 2200, "month": "演示样例"},
    {"band": "300-400万", "low": 300, "high": 400, "deals": 2600, "month": "演示样例"},
    {"band": "400-500万", "low": 400, "high": 500, "deals": 2300, "month": "演示样例"},
    {"band": "500-600万", "low": 500, "high": 600, "deals": 1600, "month": "演示样例"},
    {"band": "600万以上", "low": 600, "high": 100000, "deals": 3500, "month": "演示样例"},
]


def _read_csv(name):
    path = os.path.join(_DATA_DIR, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 %s 失败: %s", name, e)
        return None


# ---------------------------------------------------------------------------
# 数据加载（供报告卡片使用）
# ---------------------------------------------------------------------------
def load_price_bands():
    """价格段成交分布。返回 (bands, month, source)。"""
    rows = _read_csv("price_bands.csv")
    if rows:
        try:
            bands = [
                {"band": r["price_band"], "low": float(r["low"]), "high": float(r["high"]),
                 "deals": int(float(r["deals"])), "month": r.get("month", "")}
                for r in rows if r.get("price_band")
            ]
            if bands:
                return bands, bands[0].get("month", ""), SOURCE_OFFICIAL
        except Exception as e:  # noqa: BLE001
            logger.warning("price_bands.csv 解析失败: %s", e)
    return list(SEED_PRICE_BANDS), "演示样例", SOURCE_DEMO


def load_city_inventory():
    """全市在售存量房（可售）库存。返回 dict 或 None。"""
    rows = _read_csv("city_inventory.csv")
    if rows:
        try:
            r = rows[0]
            return {"as_of_date": r.get("as_of_date", ""),
                    "sets_wan": float(r["sets_wan"]), "area_wan_sqm": float(r["area_wan_sqm"])}
        except Exception as e:  # noqa: BLE001
            logger.warning("city_inventory.csv 解析失败: %s", e)
    return None


def load_district_monthly():
    """各区月度网签。官方暂未开放分区数据；有 CSV 才返回 official，否则 (None,'',None)。"""
    rows = _read_csv("district_monthly.csv")
    if rows:
        try:
            deals = {}
            month = ""
            for r in rows:
                if r.get("district") and r.get("deals"):
                    deals[r["district"].strip()] = int(float(r["deals"]))
                    month = month or (r.get("month") or "")
            if deals:
                return deals, month, SOURCE_OFFICIAL
        except Exception as e:  # noqa: BLE001
            logger.warning("district_monthly.csv 解析失败: %s", e)
    return None, "", None


def detect_district(text):
    """从小区名/地址中识别行政区；识别不到返回 None。"""
    t = (text or "").replace(" ", "")
    for d in _DISTRICTS:
        if d in t:
            return d
    return None


def get_market_benchmark(price_wan, area_sqm, location_text=""):
    """按用户报价/面积生成行情基准。返回 dict 或 None（输入非法时）。"""
    try:
        price_wan = float(price_wan)
        area_sqm = float(area_sqm)
    except (TypeError, ValueError):
        return None
    if price_wan <= 0 or area_sqm <= 0:
        return None

    bands, band_month, band_src = load_price_bands()
    total = sum(b["deals"] for b in bands)
    band = next((b for b in bands if b["low"] <= price_wan < b["high"]), None)

    d_deals, d_month, d_src = load_district_monthly()
    district = detect_district(location_text)
    rank = None
    if d_deals and district and district in d_deals:
        ranked = sorted(d_deals.items(), key=lambda kv: kv[1], reverse=True)
        rank = next((i + 1 for i, (k, _) in enumerate(ranked) if k == district), None)

    return {
        "price_wan": round(price_wan, 1),
        "unit_price": int(round(price_wan / area_sqm * 10000)),  # 元/㎡
        "band": band["band"] if band else "超出统计区间",
        "band_deals": band["deals"] if band else None,
        "band_share_pct": round(band["deals"] / total * 100, 1) if band and total else None,
        "band_month": band_month,
        "band_source": band_src,
        "inventory": load_city_inventory(),
        "district": district,
        "district_deals": d_deals.get(district) if (d_deals and district) else None,
        "district_rank": rank,
        "district_total": len(d_deals) if d_deals else 0,
        "district_source": d_src,
    }


# ---------------------------------------------------------------------------
# 数据刷新（curl 实现：本机 Python OpenSSL 与该站 TLS 不兼容）
# ---------------------------------------------------------------------------
def _curl_text(url, timeout=30):
    out = subprocess.run(
        ["curl", "-s", "--max-time", str(timeout), "-A", "Mozilla/5.0", url],
        capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"curl exit {out.returncode}")
    return out.stdout.decode("utf-8", errors="replace")


def _curl_download(url, save_path, timeout=60):
    out = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0", url, "-o", save_path],
        capture_output=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"curl exit {out.returncode}")


def refresh_from_api(user_key=None):
    """从开放平台拉取最新官方 CSV 并重建规范化数据文件。成功返回 True。

    user_key 缺省读环境变量 BJ_DATA_USERKEY。频率建议每天一次以内。
    """
    user_key = (user_key or os.environ.get("BJ_DATA_USERKEY", "")).strip()
    if not user_key:
        logger.info("BJ_DATA_USERKEY 未配置，跳过行情数据刷新")
        return False

    os.makedirs(_RAW_DIR, exist_ok=True)
    for name, fid in FILE_IDS.items():
        resp_text = _curl_text(f"{API_BASE}?id={fid}&key={user_key}")
        # 响应里混有空白行，取第一行含 JSON 的
        payload = None
        for line in resp_text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                payload = json.loads(line)
                break
        if not payload or payload.get("code") != "0":
            logger.warning("userApply[%s] 响应异常: %s", name, resp_text[:200])
            return False
        address = payload["result"]["address"]
        raw_path = os.path.join(_RAW_DIR, f"{name}_official.csv")
        _curl_download(address, raw_path)
        logger.info("已下载 %s -> %s", name, raw_path)

    rebuild_from_raw()
    return True


def _decode_bytes(b: bytes) -> str:
    """官方 CSV 编码不统一（utf-8/gbk 都出现过），自动探测。"""
    if b.startswith(b"\xef\xbb\xbf"):
        return b.decode("utf-8-sig", errors="replace")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", errors="replace")


def rebuild_from_raw():
    """把 raw/ 下的官方 CSV 转成模块使用的规范化文件。"""
    # 1) 价格段：取最新月份，映射 low/high
    price_raw = os.path.join(_RAW_DIR, "price_official.csv")
    if os.path.exists(price_raw):
        rows = list(csv.DictReader(_decode_bytes(open(price_raw, "rb").read()).splitlines()))
        latest = max(r["统计时间"] for r in rows if r.get("统计时间"))
        boundaries = {"60万以下": (0, 60), "60～90万": (60, 90), "90～120万": (90, 120),
                      "120～150万": (120, 150), "150～200万": (150, 200), "200万以上": (200, 100000)}
        out = []
        for r in rows:
            if r["统计时间"] != latest:
                continue
            lo, hi = boundaries.get(r["价格"], (None, None))
            if lo is None:
                continue
            out.append({"price_band": r["价格"], "low": lo, "high": hi,
                        "deals": int(float(r["成交套数"] or 0)), "month": latest})
        if out:
            with open(os.path.join(_DATA_DIR, "price_bands.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["price_band", "low", "high", "deals", "month"])
                w.writeheader()
                w.writerows(out)
            logger.info("price_bands.csv 重建完成（%s，%d 段）", latest, len(out))

    # 2) 全市在售库存：取各区文件的最新一行（该文件实际只有北京市全市日度数据）
    dist_raw = os.path.join(_RAW_DIR, "district_official.csv")
    if os.path.exists(dist_raw):
        rows = list(csv.DictReader(_decode_bytes(open(dist_raw, "rb").read()).splitlines()))
        if rows:
            latest = max(rows, key=lambda r: r.get("RIQI", ""))
            d = latest.get("RIQI", "")
            as_of = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
            inv = {"as_of_date": as_of,
                   "sets_wan": float(latest.get("KSQFTS") or 0),
                   "area_wan_sqm": float(latest.get("KSQFMJ") or 0)}
            if inv["sets_wan"] > 0:
                with open(os.path.join(_DATA_DIR, "city_inventory.csv"), "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=["as_of_date", "sets_wan", "area_wan_sqm"])
                    w.writeheader()
                    w.writerow(inv)
                logger.info("city_inventory.csv 重建完成（%s）", as_of)
