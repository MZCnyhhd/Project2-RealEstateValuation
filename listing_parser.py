"""房源文本解析器：把用户粘贴的房源文字信息解析成估值表单字段。

合规边界（重要，改动前先读）：
- 只解析**用户主动粘贴的文本**，本模块绝不发起任何对第三方网站（链家/贝壳/我爱我家等）的请求。
- 解析即焚：原始文本不落盘、不写日志（日志只记字段数量），只返回提取后的结构化字段。
- 只提取事实字段（价格/面积/户型/楼层/朝向等），房源描述原文、图片链接、经纪人姓名电话
  等版权/个人信息内容一律不提取、不保存。
- 解析结果必须经用户在表单中确认后才提交，本模块输出仅作预填。

正则为主（离线可用），LLM（Qwen）仅作为小区名/地址等难字段的补充增强，缺 API Key 时静默降级。
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

_DISTRICTS = ("东城|西城|朝阳|海淀|丰台|石景山|门头沟|房山|通州|顺义|昌平|大兴|怀柔|平谷|密云|延庆|亦庄|经开")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}
_DECORATION = [("豪华装修", "豪装"), ("豪装", "豪装"), ("精装修", "精装"), ("精装", "精装"),
               ("普通装修", "简装"), ("简装", "简装"), ("毛坯", "毛坯")]

_COMMUNITY_STOP = ("面积", "平米", "万", "室", "厅", "卫", "层", "朝", "装修", "地铁", "建筑",
                   "户型", "楼层", "年代", "电梯", "挂牌", "总价", "售价", "单价", "房源")

# 小区名的常见后缀词（用于从标题首段识别）
_COMMUNITY_SUFFIX = ("花园", "家园", "小区", "公寓", "新城", "新苑", "苑", "府", "湾", "郡",
                     "庭", "园", "里", "居", "院", "庄", "公馆", "广场", "大厦", "一号",
                     "号院", "城", "都", "轩", "阁", "庭园")


def looks_like_url_only(text: str) -> bool:
    """内容基本只是一个链接（没有足量中文正文）→ 拒绝解析。"""
    if not re.search(r"https?://|www\.", text, re.I):
        return False
    cn_chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
    return cn_chars < 30


def _first(patterns, text, flags=re.I):
    """按顺序尝试多个正则，返回第一个命中。"""
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m
    return None


def _parse_area(text):
    m = _first([r"建筑面积[^\d%]{0,6}(\d+(?:\.\d+)?)",
                r"(\d+(?:\.\d+)?)\s*(?:㎡|平方米|平米|m²|m2)"], text)
    if not m:
        return None
    val = float(m.group(1))
    return round(val, 1) if 20 <= val <= 800 else None


def _parse_price(text, area):
    """总价（万）。排除'X万/㎡'单价；只有单价+面积时换算总价。"""
    m = _first([r"(?:总价|售价|挂牌价|价格|卖)[：:\s]*(\d+(?:\.\d+)?)\s*万",
                r"(\d+(?:\.\d+)?)\s*万(?:元)?(?!\s*[/每]?\s*[㎡平])"], text)
    if m:
        val = float(m.group(1))
        if 30 <= val <= 30000:
            return round(val, 1)
    mu = re.search(r"(\d+(?:\.\d+)?)\s*万\s*/?\s*(?:㎡|平)", text)
    if mu and area:
        val = float(mu.group(1)) * area
        if 30 <= val <= 30000:
            return round(val, 1)
    mu2 = re.search(r"(\d{4,6})\s*元\s*/?\s*(?:㎡|平)", text)
    if mu2 and area:
        val = float(mu2.group(1)) / 10000 * area
        if 30 <= val <= 30000:
            return round(val, 1)
    return None


def _parse_layout(text):
    """返回 (rooms, halls, baths)。兼容 '4室2厅2卫' / '两居' / '开间'。"""
    m = re.search(r"(\d+)\s*室\s*(\d+)\s*厅(?:\s*(\d+)\s*卫)?", text)
    if m:
        rooms = int(m.group(1))
        halls = int(m.group(2))
        baths = int(m.group(3)) if m.group(3) else 1
        return rooms, halls, baths
    m = re.search(r"([一两二三四五\d])\s*居", text)
    if m:
        rooms = _CN_NUM.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else 2)
        return rooms, 1, 1
    if "开间" in text:
        return 1, 0, 1
    return None


def _parse_community(text):
    # 1) 带标签：小区：XX / 小区名 XX
    m = re.search(r"小区[名：:]\s*([^\s，,。；;]{2,20})", text)
    if m:
        return m.group(1).strip()
    # 2) 标题首段：行首的中文名后跟 户型/面积/价格（链家列表标题格式）
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9·]{4,16})\s+(?=\d+\s*室|\d+(?:\.\d+)?\s*(?:㎡|平)|开间|[一两二三四五]居)", line)
        if m and not any(w in m.group(1) for w in _COMMUNITY_STOP):
            return m.group(1)
    # 3) 后缀词模式
    m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12}?(?:%s))(?=[\s，,。；;（(]|$)" % "|".join(_COMMUNITY_SUFFIX), text)
    if m and not any(w in m.group(1) for w in _COMMUNITY_STOP):
        return m.group(1)
    return None


def _parse_address(text):
    m = re.search(r"(?:地址[：:\s]*)?((?:%s)区?[^，,。；;\n]{2,24})" % _DISTRICTS, text)
    if m:
        addr = m.group(1).strip()
        # 去掉把小区名误当地址开头的情况：地址里应含 区/路/街/巷/号
        if re.search(r"(区|路|街|巷|号|道)", addr):
            return addr[:30]
    return None


def _llm_fill_missing(text, fields):
    """LLM 增强：仅用于补小区名/地址等难提取字段。任何异常静默跳过。"""
    need = [k for k in ("community", "address") if not fields.get(k)]
    if not need or not os.environ.get("QWEN_API_KEY", "").strip():
        return fields
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["QWEN_API_KEY"].strip(),
            base_url=os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip(),
        )
        model = os.environ.get("QWEN_MODEL", "qwen-turbo").strip()
        prompt = ("从下面的房源信息中提取JSON：{\"community\":\"小区名\",\"address\":\"详细地址\"}。"
                  "没有的信息不要编造，对应值为 null。只输出 JSON。\n\n" + text[:1500])
        resp = client.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        import json as _json
        data = _json.loads(resp.choices[0].message.content or "{}")
        for k in need:
            v = str(data.get(k) or "").strip()
            if v and len(v) <= 25:
                fields[k] = v
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 补充解析跳过: %s", type(e).__name__)
    return fields


def parse_listing_text(text: str) -> dict:
    """解析粘贴的房源文本 → 表单字段。只返回结构化事实字段，原文不保存。"""
    fields = {}

    area = _parse_area(text)
    price = _parse_price(text, area)
    if area:
        fields["area_size"] = area
    if price:
        fields["price"] = price

    layout = _parse_layout(text)
    if layout:
        fields["layout_rooms"], fields["layout_halls"], fields["layout_baths"] = layout

    m = re.search(r"共\s*(\d{1,2})\s*层", text)
    if m:
        fields["total_floors"] = int(m.group(1))
    # 楼层：数字前不能是"共"（那是总楼层）也不能是数字（避免 28层 匹配到 8层）
    m = re.search(r"(?:^|[^\d共])(\d{1,2})\s*层", text)
    if m and int(m.group(1)) <= 80 and (not fields.get("total_floors") or int(m.group(1)) <= fields["total_floors"]):
        fields["floor"] = int(m.group(1))

    m = re.search(r"(\d{4})\s*年(?:建|建成|竣工)?", text)
    if m and 1950 <= int(m.group(1)) <= 2026:
        fields["build_year"] = int(m.group(1))

    m = re.search(r"朝([东南西北])", text)
    if m:
        fields["orientation"] = m.group(1)

    for kw, val in _DECORATION:
        if kw in text:
            fields["decoration"] = val
            break

    if "南北通透" in text or ("南北" in text and "通透" in text):
        fields["north_south"] = "1"
    if "无电梯" in text:
        fields["has_elevator"] = "0"
    elif "有电梯" in text or "电梯房" in text:
        fields["has_elevator"] = "1"

    m = re.search(r"(?:距|离)[^\n]{0,24}?(\d{2,4})\s*米", text)
    if m and 10 <= int(m.group(1)) <= 5000:
        fields["subway_distance"] = int(m.group(1))

    community = _parse_community(text)
    if community:
        fields["community"] = community
    address = _parse_address(text)
    if address:
        fields["address"] = address

    fields = _llm_fill_missing(text, fields)

    missing = [k for k in ("community", "area_size", "price") if not fields.get(k)]
    return {"fields": fields, "filled_count": len(fields), "missing": missing}


def parse_listing_text_safe(text: str) -> dict:
    """对外的安全封装：任何异常都不外抛（解析失败返回空结果，不阻塞用户手填）。"""
    try:
        return parse_listing_text(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("粘贴解析异常: %s", type(e).__name__)
        return {"fields": {}, "filled_count": 0, "missing": ["community", "area_size", "price"]}


# ---------------------------------------------------------------------------
# 截图视觉解析（Qwen-VL）：用户粘贴房源详情页截图 → 提取事实字段
# 合规边界同上：只处理用户主动提交的图片，不发起对第三方网站的请求；
# 图片即焚（不落盘、不写日志内容），只提取事实字段，需用户确认后提交。
# ---------------------------------------------------------------------------
_ORIENTATIONS = {"南", "东南", "西南", "东", "西", "北"}
_DECOR_SET = {"毛坯", "简装", "精装", "豪装"}
_ELEV_RATIO = {"1T2", "2T4", "2T6", "3T8"}
_CN_DIGIT = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "八": 8}

VL_PROMPT = """你是房产信息提取助手。图片是二手房详情页截图（可能包含"基本信息""房源特色""户型分间尺寸表"等板块）。
请只提取图片中**明确写了的客观事实**，输出 JSON（不要输出任何其他文字）：
{
  "community": "小区名，若图中没有则为 null",
  "address": "详细地址，若没有则为 null",
  "area_size": 建筑面积数字(㎡),
  "price": "若图中出现总价（万）则填数字，否则 null",
  "layout_rooms": 室数, "layout_halls": 厅数, "layout_kitchens": 厨房数, "layout_baths": 卫数,
  "floor": "所在楼层数字，如'低楼层'这种描述没有具体数字则 null",
  "total_floors": 总楼层数字,
  "orientation": "房屋朝向，只能是 南/东南/西南/东/西/北 之一，否则 null",
  "decoration": "装修情况，只能是 毛坯/简装/精装/豪装 之一，否则 null",
  "north_south": "南北通透填 1，否则 0",
  "has_elevator": "配备电梯有填 1 无填 0，没有提及则 null",
  "elevator_ratio": "梯户比例，如 三梯八户 → 3T8，两梯四户 → 2T4",
  "five_year_only": "满五年/满五唯一填 1，否则 0",
  "has_mortgage": "有抵押填 1，无抵押填 0，没有提及则 null",
  "subway_distance": "距地铁的米数数字，没有则 null"
}
规则：数字去掉单位；没有的信息填 null；禁止编造。"""


def _normalize_vl_output(raw: dict) -> dict:
    """把 VL 返回的 JSON 清洗成表单字段（白名单键 + 值域校验）。"""
    fields = {}

    def _num(key, lo, hi):
        v = raw.get(key)
        if v is None or v == "":
            return
        m = re.search(r"-?\d+(?:\.\d+)?", str(v))  # 容忍 '300米'/'127.85㎡' 这类带单位输出
        if not m:
            return
        try:
            v = float(m.group(0))
        except (TypeError, ValueError):
            return
        if lo <= v <= hi:
            fields[key] = round(v, 1) if v != int(v) else int(v)

    _num("area_size", 20, 800)
    _num("price", 30, 30000)
    _num("layout_rooms", 0, 10)
    _num("layout_halls", 0, 8)
    _num("layout_kitchens", 0, 5)
    _num("layout_baths", 0, 5)
    _num("floor", 1, 80)
    _num("total_floors", 1, 99)
    _num("subway_distance", 10, 5000)
    if fields.get("floor") and fields.get("total_floors") and fields["floor"] > fields["total_floors"]:
        fields.pop("floor")

    for key, allowed in (("orientation", _ORIENTATIONS), ("decoration", _DECOR_SET)):
        v = str(raw.get(key) or "").strip()
        if v in allowed:
            fields[key] = v

    me = re.search(r"([一二两三四五六八\d])\s*梯\s*([一二两三四五六八\d])\s*户", str(raw.get("elevator_ratio") or ""))
    if me:
        a = _CN_DIGIT.get(me.group(1), me.group(1))
        b = _CN_DIGIT.get(me.group(2), me.group(2))
        candidate = f"{a}T{b}"
        if candidate in _ELEV_RATIO:
            fields["elevator_ratio"] = candidate

    for key in ("community", "address"):
        v = str(raw.get(key) or "").strip()
        if v and 1 < len(v) <= 25 and not any(w in v for w in _COMMUNITY_STOP):
            fields[key] = v

    for key in ("north_south", "has_elevator", "five_year_only", "has_mortgage"):
        v = raw.get(key)
        if v is not None and str(v) in ("0", "1"):
            fields[key] = str(v)
    return fields


def parse_images_with_vl(data_uris):
    """调用 Qwen-VL 解析截图。返回 (fields, error)；无 Key 时 error 提示配置。"""
    api_key = os.environ.get("QWEN_API_KEY", "").strip()
    if not api_key:
        return {}, ("图片解析需要 AI 视觉服务：请在环境变量配置 QWEN_API_KEY（阿里云百炼），"
                    "文字粘贴解析不受影响")
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip(),
        )
        model = os.environ.get("QWEN_VL_MODEL", "qwen-vl-max").strip()
        content = [{"type": "text", "text": VL_PROMPT}]
        for uri in data_uris:
            content.append({"type": "image_url", "image_url": {"url": uri}})
        resp = client.chat.completions.create(model=model, temperature=0, messages=[
            {"role": "user", "content": content}])
        out = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return {}, "AI 未返回有效结果，请重试或改用文字粘贴"
        import json as _json
        raw = _json.loads(m.group(0))
        return _normalize_vl_output(raw if isinstance(raw, dict) else {}), None
    except Exception as e:  # noqa: BLE001
        logger.warning("VL 解析失败: %s %s", type(e).__name__, str(e)[:120])
        return {}, ("AI 解析调用失败，请稍后重试或改用文字粘贴"
                    f"（{type(e).__name__}）")
