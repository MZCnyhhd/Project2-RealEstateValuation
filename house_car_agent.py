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

# ============ 车型库（小米汽车）============
# ⚠️ 参数为示例口径（价格/续航为公开指导值，其余为参考值），展示时统一提醒"以门店实车为准"
CARS = [
    {
        "name": "小米SU7", "price": 21.59, "range": 700, "type": "C级轿跑",
        "tag": "人车家全生态 · 高颜值轿跑",
        "battery": "73.6kWh 磷酸铁锂", "fast_charge": "快充 15 分钟补能约 350km",
        "highlights": ["风阻系数 0.195Cd", "0-100km/h 5.28s", "小米澎湃智能座舱"],
        "suitable": "第一台纯电轿跑、注重颜值与智能生态",
        "demand_keywords": ["轿跑", "颜值", "代步", "入门", "性价比", "第一台", "智能", "轿车"],
    },
    {
        "name": "小米SU7 Pro", "price": 24.59, "range": 830, "type": "C级轿跑",
        "tag": "长续航 · 城市NOA辅助驾驶",
        "battery": "94.3kWh", "fast_charge": "871V 高压平台快充 15 分钟补能约 450km",
        "highlights": ["CLTC 830km 超长续航", "城市领航辅助（NOA）", "激光雷达"],
        "suitable": "长途需求多、想要高阶智驾的轿跑用户",
        "demand_keywords": ["续航", "长途", "智驾", "辅助驾驶", "轿跑", "激光雷达", "NOA"],
    },
    {
        "name": "小米SU7 Max", "price": 29.99, "range": 800, "type": "C级性能轿跑",
        "tag": "双电机四驱 · 零百 2.78s",
        "battery": "101kWh 三元锂", "fast_charge": "871V 高压平台快充 15 分钟补能约 500km",
        "highlights": ["零百加速 2.78s", "双电机四驱", "Brembo 四活塞卡钳+CDC悬架"],
        "suitable": "追求性能与豪华配置全部拉满的用户",
        "demand_keywords": ["性能", "加速", "四驱", "运动", "豪华", "旗舰", "轿跑"],
    },
    {
        "name": "小米SU7 Ultra", "price": 52.99, "range": 630, "type": "高性能旗舰轿跑",
        "tag": "三电机 1548PS · 纽北同源技术",
        "battery": "93.7kWh 赛道级高功率电池", "fast_charge": "快充 11 分钟 10%→80%",
        "highlights": ["零百加速 1.98s", "最高时速 350km/h", "碳陶瓷制动盘可选"],
        "suitable": "性能发烧友、赛道玩家、预算充足的旗舰用户",
        "demand_keywords": ["ultra", "超跑", "赛道", "性能", "速度", "旗舰", "顶级", "发烧"],
    },
    {
        "name": "小米YU7", "price": 25.35, "range": 835, "type": "中大型SUV",
        "tag": "SUV 续航标杆 · 人车家全生态",
        "battery": "96.3kWh 磷酸铁锂", "fast_charge": "800V 级高压平台快充 15 分钟补能约 620km",
        "highlights": ["CLTC 835km 同级超长续航", "2000MPa 级高强度钢铝合金车身", "全天候防紫外线天幕"],
        "suitable": "家庭首购主力 SUV、注重续航与安全",
        "demand_keywords": ["SUV", "家用", "空间", "家庭", "续航", "长途", "安全", "一家人"],
    },
    {
        "name": "小米YU7 Pro", "price": 27.99, "range": 770, "type": "中大型SUV",
        "tag": "高阶智驾 SUV · 城市NOA",
        "battery": "96.3kWh 三元锂", "fast_charge": "800V 级高压平台快充 15 分钟补能约 620km",
        "highlights": ["城市领航辅助（NOA）", "激光雷达", "后排座椅电动调节"],
        "suitable": "要智驾要空间的家庭用户",
        "demand_keywords": ["SUV", "智驾", "辅助驾驶", "家庭", "家用", "智能", "NOA", "空间"],
    },
    {
        "name": "小米YU7 Max", "price": 32.99, "range": 760, "type": "中大型性能SUV",
        "tag": "双电机四驱 · 零百 3.23s",
        "battery": "101.7kWh", "fast_charge": "800V 级高压平台快充 15 分钟补能约 620km",
        "highlights": ["零百加速 3.23s", "双电机四驱", "CDC 可变阻尼减振器+空气弹簧"],
        "suitable": "预算充足的品质家庭、要性能也要舒适",
        "demand_keywords": ["SUV", "四驱", "性能", "旗舰", "豪华", "家庭", "自驾游", "空气悬架"],
    },
]

# ============ FAQ 知识库 ============
KB = [
    {"intent": "房贷咨询", "keywords": ["房贷", "首付", "贷款", "利率", "月供", "等额", "商贷", "公积金"],
     "a": "房贷速查（北京）：\n• 首套：首付 20% 起，商贷利率 3.05% 左右\n• 二套（普宅）：首付 30% 起；非普宅 35%\n• 公积金：5 年以上利率 2.85%\n💡 精确测算请用【计算器】页面，或直接对我说「预算600万，首套，贷30年」。"},
    {"intent": "购房政策", "keywords": ["购房资格", "限购", "社保", "资质", "过户", "税费", "契税"],
     "a": "北京购房要点：\n• 契税：首套 90㎡ 以下 1%，以上 1.5%；二套 3%\n• 增值税：满 2 年免征（非普宅差额征收）\n• 限购：京籍家庭 2 套、单身 1 套\n资质核验可预约门店顾问协助办理。"},
    {"intent": "充电续航", "keywords": ["充电", "快充", "慢充", "续航", "电池", "电量", "桩"],
     "a": "小米汽车补能速览：\n⚡ 871V 碳化硅高压平台（SU7 Pro/Max/Ultra、YU7 全系）：快充 15 分钟补能 450-620km\n🔌 家充桩：7kW 交流慢充，夜间谷电一晚满电\n🔋 电池：磷酸铁锂/三元锂双路线，支持预约充电利用谷电\n📍 全国小米超充站持续铺设中，App 内可查桩。具体以门店实车为准。"},
    {"intent": "售后保养", "keywords": ["保养", "维修", "年检", "首保", "质保", "三电"],
     "a": "小米汽车用车成本（以官方权益为准）：\n• 保养：电车结构简单，常规小保养约 300-500 元/次，1 年或 2 万公里一次\n• 质保：整车 + 三电系统质保，具体年限随官方权益公布\n• 服务：App 一键预约维保，透明工时价\n💡 回复「转人工」可获取您所在城市的服务中心地址。"},
    {"intent": "看房试驾预约", "keywords": ["预约", "看房", "试驾", "到店", "门店"],
     "a": "已收到您的意向 📋\n• 预约看房：留下区域+预算，顾问 1 小时内联系您安排\n• 预约试驾：SU7 / YU7 全系 7 款车型可试，到店即赠精美好礼\n也可以直接说「预算500万推荐个三居室」，我先为您线上初筛。"},
    {"intent": "购车优惠金融", "keywords": ["优惠", "补贴", "置换", "金融", "分期", "贷款买车", "首付", "权益", "赠送"],
     "a": "购车权益速览（以门店当期政策为准）：\n• 置换补贴：至高 8000 元/台（旧车评估后叠加）\n• 金融方案：首付低至 20%，24 期 0 息可选\n• 下订权益：赠充电桩+免费安装、首年交强险\n💡 告诉我预算，我帮您匹配哪款优惠后最划算～"},
    {"intent": "绿牌政策", "keywords": ["绿牌", "牌照", "上牌", "限行", "购置税", "指标"],
     "a": "新能源用车政策（北京，以最新政策为准）：\n• 免征车辆购置税\n• 不受工作日尾号限行限制\n• 上牌：新能源指标单独排队，购车后凭发票/合格证办理\n💡 具体指标政策建议以北京小客车指标调控官网为准，或回复「转人工」详询。"},
    {"intent": "竞品对比", "keywords": ["特斯拉", "model", "比亚迪", "小鹏", "蔚来", "理想", "极氪", "智界", "问界", "极越"],
     "a": "特斯拉、比亚迪这些都是很优秀的新能源品牌 👍 不做贬低，只帮您对需求：\n• 20 万级智能轿跑：SU7 可与 Model 3 同场对比\n• 25 万级家用 SUV：YU7 的 835km 续航与空间有优势\n• 30 万级性能：SU7 Max 零百 2.78s\n需要的话我为您整理具体参数对比表，或预约同场对比试驾～"},
    {"intent": "投诉建议", "keywords": ["投诉", "不满", "态度", "差评"],
     "a": "非常抱歉给您带来不好的体验 🙏 可回复「转人工」30 秒接入专属客服，或拨打 400-xxx-xxxx（7×24 小时），24 小时内专人跟进。"},
]

HUMAN_KEYWORDS = ["人工", "真人", "客服人员", "坐席"]
GREETINGS = ["你好", "您好", "hi", "hello", "在吗", "嗨"]
REGIONS = ["朝阳", "海淀", "昌平", "大兴", "通州", "丰台", "西城", "东城", "亦庄", "顺义", "石景山", "门头沟", "房山"]
ROOM_NUM = {"一": 1, "1": 1, "二": 2, "两": 2, "2": 2, "三": 3, "3": 3, "四": 4, "4": 4}
# 购车权益/服务类词：命中时优先走 FAQ，不做车型推荐（避免"有什么优惠"被当成选车需求）
CAR_SERVICE_WORDS = ["优惠", "补贴", "置换", "分期", "金融", "免息", "保养", "维修",
                     "质保", "投诉", "绿牌", "购置税", "上牌", "指标", "保险", "权益"]


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


def _demand_bonus(text: str, car: dict) -> int:
    """用户需求词与车型标签的匹配加分（同预算内优先推荐更贴合的车）。

    用户直接点名车型（如「YU7」「SU7 Ultra」）时给强加分。
    """
    bonus = sum(2 for k in car.get("demand_keywords", []) if k in text)
    core = car["name"].replace("小米", "").lower()
    if core and core in text.lower():
        bonus += 10
    return bonus


def _car_card(c: dict) -> str:
    """单车型多行卡片文案。"""
    lines = [
        f"🚗 {c['name']}",
        f"💰 {c['price']:g} 万起 | 🔋 续航 {c['range']}km | 🚙 {c['type']}",
    ]
    if c.get("battery"):
        lines.append(f"⚡ {c['battery']} 电池 · {c.get('fast_charge', '支持快充')}")
    if c.get("highlights"):
        lines.append(f"✓ {' · '.join(c['highlights'][:3])}")
    if c.get("suitable"):
        lines.append(f"👨‍👩‍👧 适合：{c['suitable']}")
    return "\n".join(lines)


def recommend_car(text: str):
    # 权益/服务类问题（优惠、分期、保养等）交给 FAQ，不做车型推荐
    if any(w in text for w in CAR_SERVICE_WORDS):
        return None
    budget = _parse_budget(text)
    rng = _parse_range(text)
    want_suv = bool(re.search(r"suv", text, re.IGNORECASE))
    want_mpv = ("mpv" in text or "面包" in text or "侧滑" in text)
    want_sedan = ("轿车" in text or "家轿" in text or "轿跑" in text)
    # 用户点名品牌/车型（如「小米」「YU7」「SU7 Ultra」）也算选车意图
    brand_hit = "小米" in text or any(c["name"].replace("小米", "").lower() in text for c in CARS)
    if (budget is None and rng is None and not want_suv and not want_mpv
            and not want_sedan and "车" not in text and not brand_hit):
        return None

    def _type_ok(c):
        t = c["type"].upper()
        if want_suv and "SUV" not in t:
            return False
        if want_mpv and "MPV" not in t:
            return False
        if want_sedan and ("SUV" in t or "MPV" in t):
            return False
        return True

    picked = [c for c in CARS
              if _type_ok(c)
              and (budget is None or c["price"] <= budget * 1.15)
              and (rng is None or c["range"] >= rng * 0.9)]
    relaxed = False
    if not picked:  # 放宽预算，保续航/车型
        relaxed = True
        picked = [c for c in CARS if _type_ok(c) and (rng is None or c["range"] >= rng * 0.9)]
    if not picked:
        return None
    # 同条件内：需求词命中多的优先，其次价格贴近预算的优先
    picked.sort(key=lambda c: -_demand_bonus(text, c) * 100 + abs(c["price"] - (budget or c["price"])))
    picked = picked[:2]

    lines = [_car_card(c) for c in picked]
    head = "根据您的需求"
    if budget is not None:
        head += f"（预算 {budget:g} 万）"
    if rng is not None:
        head += f"（续航 {rng}km+）"
    head += f"，为您精选 {len(picked)} 款车型：\n\n"
    if relaxed:
        head = "⚠️ 您的预算内暂无精确匹配，已放宽条件为您推荐最接近的：\n\n" + head
    tail = ("\n\n想亲自开一圈？回复「预约试驾」～\n"
            "📎 车型参数为参考值，具体配置与价格以门店实车为准。")
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
