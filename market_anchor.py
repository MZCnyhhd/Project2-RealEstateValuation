"""市场基准锚点：给估值提供一个"不依赖用户自填报价"的定价起点。

背景（2026-10-04 审计结论）：原实现把用户自己填的挂牌价当基准，再叠加维度涨跌，
等于"用挂牌价验证挂牌价"，系统性偏差无法收敛。本模块把基准价改成可插拔来源：

优先级（自上而下）
  1. 小区样本锚点：同小区已有房源（用户回流的真实挂牌 + Demo 数据）单价中位数 × 本套面积
     —— 样本数 ≥ MIN_SAMPLES 才采信，避免被单套异常报价带偏
  2. 区域参考区间：data_beijing/price_bands.csv（官方全市总价段）反推的全市单价区间，
     仅用于提示"当前报价是否明显偏离全市区间"，不做定价
  3. 用户挂牌价：兜底（source='用户挂牌价'，confidence='低'）

数据边界（合规）：
- 只使用本平台自身积累的房源（用户主动提交后回流）+ 官方公开统计数据；
- 不抓取任何第三方平台数据；样本为挂牌价而非成交价，报告中如实标注。
"""
import json
import logging
import os
import statistics

logger = logging.getLogger(__name__)

MIN_SAMPLES = 3          # 同小区至少 3 套才采信中位单价
UNIT_FLOOR = 1.5        # 单价下限（万/㎡），过滤明显异常数据
UNIT_CEIL = 30.0        # 单价上限（万/㎡）
# 挂牌价 → 成交价折价系数（北京二手房经验值：成交价通常低于挂牌 3~5%；
# 属行业经验假设，报告中如实标注，接入真实成交样本后可校准）
LISTING_TO_DEAL = 0.96

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_beijing")


def _norm_name(name: str) -> str:
    """小区名归一：去空格、去常见后缀差异，便于同小区匹配。"""
    s = str(name or "").strip()
    for sep in ("·", " ", "-", "—"):
        s = s.replace(sep, "")
    return s


def collect_community_samples(listings) -> dict:
    """从房源列表里按小区聚合单价样本（万/㎡）。返回 {小区名: [单价...]}"""
    pools = {}
    for it in listings or []:
        name = _norm_name(it.get("community", ""))
        area = it.get("area")
        price = it.get("price")
        if not name or not area or not price:
            continue
        try:
            unit = float(price) / float(area)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if UNIT_FLOOR <= unit <= UNIT_CEIL:
            pools.setdefault(name, []).append(round(unit, 3))
    return pools


def _city_unit_band() -> tuple:
    """官方全市总价段 → 反推全市单价区间（弱参考，仅用于偏离提示）。"""
    path = os.path.join(_DATA_DIR, "price_bands.csv")
    if not os.path.exists(path):
        return None
    units = []
    try:
        import csv
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                band = str(row.get("price_band", ""))
                deals = float(row.get("deals") or 0)
                if not deals or "以下" in band or "以上" in band:
                    continue
                low, high = float(row.get("low") or 0), float(row.get("high") or 0)
                # 用该总价段常见面积（60~140㎡ 中位 100㎡）粗估单价
                if high > 0:
                    units.append(high / 100.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("官方价格段解析失败: %s", exc)
        return None
    if not units:
        return None
    return round(min(units), 2), round(statistics.median(units), 2)


def get_baseline(community: str, area_size, user_price, listings=None,
                 extra_samples=None) -> dict:
    """返回 {"baseline", "source", "confidence", "unit_price", "samples", "note"}"""
    area = None
    try:
        area = float(area_size)
    except (TypeError, ValueError):
        area = None
    user_price = float(user_price or 0)

    pools = collect_community_samples(listings)
    for name, units in (extra_samples or {}).items():
        pools.setdefault(_norm_name(name), []).extend(units)

    name = _norm_name(community)
    units = pools.get(name, [])
    if area and len(units) >= MIN_SAMPLES:
        unit = round(statistics.median(units) * LISTING_TO_DEAL, 3)
        return {
            "baseline": round(unit * area, 1),
            "source": "同小区房源样本（折算成交口径）",
            "confidence": "中",
            "unit_price": unit,
            "samples": len(units),
            "note": (f"取自本平台 {len(units)} 套同小区房源挂牌单价中位数，"
                     f"按行业经验折价 {int((1 - LISTING_TO_DEAL) * 100)}% 折算为成交口径 {unit} 万/㎡"),
        }

    band = _city_unit_band()
    note = ""
    if user_price > 0 and area:
        unit = round(user_price / area, 2)
        if band:
            lo, mid = band
            if unit < lo * 0.6:
                note = f"当前单价 {unit} 万/㎡ 明显低于全市总价段反推区间（约 {lo} 万/㎡ 起），请核对面积或价格"
            elif unit > mid * 1.8:
                note = f"当前单价 {unit} 万/㎡ 明显高于全市常见水平（约 {mid} 万/㎡），注意评估风险"
    return {
        "baseline": round(user_price * LISTING_TO_DEAL, 1) if user_price else 0,
        "source": "用户挂牌价（折算成交口径）" if user_price else "无",
        "confidence": "低" if user_price else "无",
        "unit_price": round(user_price * LISTING_TO_DEAL / area, 2) if (user_price and area) else None,
        "samples": len(units),
        "note": note or (f"同小区样本仅 {len(units)} 套（需 ≥{MIN_SAMPLES} 套才采信），暂以您的挂牌价"
                         f"折价 {int((1 - LISTING_TO_DEAL) * 100)}% 作为成交口径基准" if units else
                         "暂无同小区样本，暂以您的挂牌价折算成交口径作为基准"),
    }
