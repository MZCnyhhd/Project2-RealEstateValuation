"""房·车智能推荐客服 Agent。

设计延续本项目"LLM 主引擎 + 规则引擎降级"的双引擎架构：
- 推荐类请求走结构化筛选排序（预算/居室/区域/续航/车型）
- FAQ 类走关键词打分检索
- 规则引擎无法识别时返回 None，由上层路由到 LLM 通用客服，LLM 失败再兜底

房源数据复用项目房源库（repository），车型库为本模块内置数据。
"""

import logging
import re
from typing import Dict, Optional, Tuple

from repository import get_repository

logger = logging.getLogger(__name__)

# ============ 车型库 ============
CARS = [
    {"name": "EC5", "price": 10.99, "range": 403, "type": "SUV", "tag": "城市代步 · 高性价比"},
    {"name": "EX3", "price": 11.50, "range": 501, "type": "小型SUV", "tag": "续航扎实 · 灵活好停"},
    {"name": "EU5 Plus", "price": 12.99, "range": 501, "type": "轿车", "tag": "家轿销量款 · 口碑好"},
    {"name": "EU7", "price": 17.99, "range": 451, "type": "轿车", "tag": "商务质感 · 大空间"},
    {"name": "极狐阿尔法S", "price": 22.58, "range": 602, "type": "中大型轿车", "tag": "长续航 · 智能座舱"},
    {"name": "极狐阿尔法T", "price": 24.98, "range": 480, "type": "中型SUV", "tag": "家庭出行 · 高阶智驾"},
]

# ============ FAQ 知识库 ============
KB = [
    {"intent": "房贷咨询", "keywords": ["房贷", "首付", "贷款", "利率", "月供", "等额", "商贷", "公积金"],
     "a": "房贷速查（北京）：\n• 首套：首付 20% 起，商贷利率 3.05% 左右\n• 二套（普宅）：首付 30% 起；非普宅 35%\n• 公积金：5 年以上利率 2.85%\n💡 精确测算请用【计算器】页面，或直接对我说「预算600万，首套，贷30年」。"},
    {"intent": "购房政策", "keywords": ["购房资格", "限购", "社保", "资质", "过户", "税费", "契税"],
     "a": "北京购房要点：\n• 契税：首套 90㎡ 以下 1%，以上 1.5%；二套 3%\n• 增值税：满 2 年免征（非普宅差额征收）\n• 限购：京籍家庭 2 套、单身 1 套\n资质核验可预约门店顾问协助办理。"},
    {"intent": "充电续航", "keywords": ["充电", "快充", "慢充", "续航", "电池", "电量", "桩"],
     "a": "全系车型支持：\n⚡ 快充：30% → 80% 约 30 分钟\n🔌 慢充：6-8 小时充满\n🔋 电池质保 8 年/15 万公里，购车赠送充电桩 + 免费安装。"},
    {"intent": "售后保养", "keywords": ["保养", "维修", "年检", "首保", "质保"],
     "a": "保养周期：首保 3000km/3 个月（免费），常规每 1 万公里或 12 个月一次，主要检查三电系统与易损件，单次约 300-500 元。可在 App【服务】一键预约。"},
    {"intent": "看房试驾预约", "keywords": ["预约", "看房", "试驾", "到店", "门店"],
     "a": "已收到您的意向 📋\n• 预约看房：留下区域+预算，顾问 1 小时内联系您安排\n• 预约试驾：6 款车型可试，到店即赠精美好礼\n也可以直接说「预算500万推荐个三居室」，我先为您线上初筛。"},
    {"intent": "竞品对比", "keywords": ["小米", "su7", "yu7", "特斯拉", "比亚迪", "小鹏", "蔚来", "理想", "model"],
     "a": "小米、特斯拉这些都是很优秀的新能源品牌 👍 不做贬低，只帮您对需求：\n• 关注 15 万级家用纯电：可对标我们的 EU5 Plus（501km 续航）\n• 关注 25 万级长续航 + 智能座舱：极狐阿尔法S（602km）值得对比试驾\n需要的话我为您整理具体参数对比表，或预约同场对比试驾～"},
    {"intent": "投诉建议", "keywords": ["投诉", "不满", "态度", "差评"],
     "a": "非常抱歉给您带来不好的体验 🙏 可回复「转人工」30 秒接入专属客服，或拨打 400-xxx-xxxx（7×24 小时），24 小时内专人跟进。"},
]

HUMAN_KEYWORDS = ["人工", "真人", "客服人员", "坐席"]
GREETINGS = ["你好", "您好", "hi", "hello", "在吗", "嗨"]
REGIONS = ["朝阳", "海淀", "昌平", "大兴", "通州", "丰台", "西城", "东城", "亦庄", "顺义", "石景山", "门头沟", "房山"]
ROOM_NUM = {"一": 1, "1": 1, "二": 2, "两": 2, "2": 2, "三": 3, "3": 3, "四": 4, "4": 4}


def _parse_budget(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    return float(m.group(1)) if m else None


def _parse_rooms(text: str) -> Optional[int]:
    m = re.search(r"([一二两三四1-4])\s*居", text)
    if m:
        return ROOM_NUM.get(m.group(1))
    m = re.search(r"([1-4])\s*室", text)
    return int(m.group(1)) if m else None


def _parse_range(text: str) -> Optional[int]:
    m = re.search(r"续航\s*(\d{3})", text)
    return int(m.group(1)) if m else None


def _listing_rooms(layout: str) -> int:
    m = re.match(r"(\d+)", layout or "")
    return int(m.group(1)) if m else 0


def recommend_house(text: str):
    """从房源库按预算/居室/区域筛选，返回 (推荐文案, 是否放宽条件)。"""
    budget = _parse_budget(text)
    rooms = _parse_rooms(text)
    region = next((r for r in REGIONS if r in text), None)
    if budget is None and rooms is None and region is None:
        return None
    listings = get_repository().list_listings()

    def match(h):
        ok = True
        if budget is not None:
            ok = ok and h.get("price", 0) <= budget * 1.15
        if rooms is not None:
            ok = ok and _listing_rooms(h.get("layout", "")) == rooms
        if region is not None:
            hay = (h.get("address", "") or "") + (h.get("community", "") or "")
            ok = ok and region in hay
        return ok

    picked = [h for h in listings if match(h)]
    relaxed = False
    if not picked:  # 放宽预算，保居室/区域
        relaxed = True
        picked = [h for h in listings if (rooms is None or _listing_rooms(h.get("layout", "")) == rooms)
                  and (region is None or region in ((h.get("address", "") or "") + (h.get("community", "") or "")))]
    if not picked:
        return None
    picked.sort(key=lambda h: abs(h.get("price", 0) - (budget or h.get("price", 0))))
    picked = picked[:2]

    lines = []
    for h in picked:
        lines.append(
            f"🏠 {h.get('community', '')}（{h.get('address', '')}）\n"
            f"💰 {h.get('price', 0):g} 万 | 🛏 {h.get('layout', '')} | 📐 {h.get('area', 0):g}㎡\n"
            f"✓ {' · '.join((h.get('tags') or [])[:3])}\n"
            f"详情：/listing/{h.get('id', '')}"
        )
    head = "根据您的需求"
    if budget is not None:
        head += f"（预算 {budget:g} 万）"
    if rooms is not None:
        head += f"（{rooms} 居）"
    if region is not None:
        head += f"（{region}）"
    head += f"，为您精选 {len(picked)} 套房源：\n\n"
    if relaxed:
        head = "⚠️ 您的预算内暂无精确匹配，已放宽条件为您推荐最接近的：\n\n" + head
    tail = "\n\n对哪套感兴趣？回复「预约看房」安排实地带看～"
    return head + "\n\n".join(lines) + tail, relaxed


def recommend_car(text: str):
    budget = _parse_budget(text)
    rng = _parse_range(text)
    want_suv = bool(re.search(r"suv", text, re.IGNORECASE))
    want_sedan = ("轿车" in text or "家轿" in text)
    if budget is None and rng is None and not want_suv and not want_sedan and "车" not in text:
        return None
    picked = [c for c in CARS
              if (budget is None or c["price"] <= budget * 1.15)
              and (rng is None or c["range"] >= rng * 0.9)
              and (not want_suv or "SUV" in c["type"].upper())
              and (not want_sedan or "SUV" not in c["type"].upper())]
    relaxed = False
    if not picked:
        relaxed = True
        picked = [c for c in CARS if rng is None or c["range"] >= rng * 0.9]
    if not picked:
        return None
    picked.sort(key=lambda c: abs(c["price"] - (budget or c["price"])))
    picked = picked[:2]

    lines = []
    for c in picked:
        lines.append(
            f"🚗 {c['name']}\n💰 {c['price']:g} 万 | 🔋 续航 {c['range']}km | 🚙 {c['type']}\n✓ {c['tag']}"
        )
    head = "根据您的需求"
    if budget is not None:
        head += f"（预算 {budget:g} 万）"
    if rng is not None:
        head += f"（续航 {rng}km+）"
    head += f"，为您精选 {len(picked)} 款车型：\n\n"
    if relaxed:
        head = "⚠️ 您的预算内暂无精确匹配，已放宽条件为您推荐最接近的：\n\n" + head
    tail = "\n\n想亲自开一圈？回复「预约试驾」～"
    return head + "\n\n".join(lines) + tail, relaxed


def build_house_car_reply(message: str) -> Tuple[Optional[str], Dict]:
    """规则引擎主入口。返回 (回复文本, 元信息)；回复为 None 表示规则引擎无法识别。"""
    text = (message or "").strip().lower()
    if not text:
        return "请输入您的问题～", {"intent": "空输入"}

    if any(k in text for k in HUMAN_KEYWORDS):
        return ("已为您转接人工客服 👩‍💼\n当前排队 1 人，预计等待 30 秒…（人工坐席服务时间 9:00-21:00）",
                {"intent": "转人工", "engine": "rule"})

    if any(text == g for g in GREETINGS):
        return ("您好 👋 我是房·车智能推荐助手！\n🏠 按预算/居室/区域为您推荐房源\n🚗 按预算/续航/车型为您推荐车辆\n例如：「预算500万推荐个三居室」「20万左右推荐辆续航500的电车」",
                {"intent": "闲聊", "engine": "rule"})

    hr = recommend_house(text) if re.search(r"房|楼盘|小区|居|平米|学区|购房|首付", text) else None
    if hr:
        return hr[0], {"intent": "房源推荐", "engine": "rule", "relaxed": hr[1]}
    cr = recommend_car(text)
    if cr:
        return cr[0], {"intent": "车型推荐", "engine": "rule", "relaxed": cr[1]}

    best, best_score = None, 0
    for item in KB:
        score = sum(1 for k in item["keywords"] if k in text)
        if score > best_score:
            best_score, best = score, item
    if best and best_score >= 1:
        return best["a"], {"intent": best["intent"], "engine": "rule"}

    return None, {"intent": "未识别", "engine": "rule"}
