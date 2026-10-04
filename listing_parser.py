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
    if not need or not os.environ.get("MIMO_API_KEY", "").strip():
        return fields
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=os.environ["MIMO_API_KEY"].strip(),
            base_url=os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip(),
        )
        model = os.environ.get("MIMO_MODEL", "mimo-v2.5").strip()
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

    m = re.search(r"朝([东南西北]{1,2})", text)
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
_ORIENTATIONS = {"南", "南北", "东南", "西南", "东", "西", "北"}
_DECOR_SET = {"毛坯", "简装", "精装", "豪装"}
_ELEV_RATIO = {"1T2", "2T4", "2T6", "3T8"}
_CN_DIGIT = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "八": 8}

VL_PROMPT = """你是房产信息提取助手。输入为二手房详情页/户型图截图。只提取图中明确写明的客观事实，输出纯 JSON（不要任何解释文字），无信息一律填 null。数字去掉单位；枚举值只取给定选项；禁止编造。
{
  "community": "小区名", "address": "详细地址",
  "area_size": 建筑面积(㎡), "interior_area": 套内面积(㎡, 链家标"套内面积"那一行，务必单独提取), "price": 总价(万),
  "layout_rooms": 室数, "layout_halls": 厅数, "layout_kitchens": 厨数, "layout_baths": 卫数,
  "floor_frac": 楼层"所在/总"如 2/6(图中明确写出所在楼层才填; 只写"共6层"或"中楼层"即未知楼层, 填 null),
  "orientation": 仅 "南"/"南北"/"东南"/"西南"/"东"/"西"/"北",
  "decoration": 仅 "毛坯"/"简装"/"精装"/"豪装",
  "layout_structure": 仅 "平层"/"错层"/"跃层"/"复式",
  "building_type": 仅 "塔楼"/"板楼"/"塔板结合",
  "building_structure": 钢混/砖混/混合/钢结构/砖木(去掉"结构"二字),
  "master_area": 主卧面积(㎡), "living_area": 客厅面积(㎡),
  "second_bedroom_area": 次卧较大一间面积(㎡),
  "study_area": 书房面积(㎡), "entrance_area": 玄关面积(㎡),
  "room_detail": "客厅 19.11平米 南 普通窗；卧室A 10.23平米 南 普通窗；过道A 1.32平米 无 无窗" 每间分号分隔(房间名/面积/朝向/窗型),
  "last_trade_year": 上次交易年份,
  "list_date": 挂牌时间(YYYY-MM-DD),
  "cert_photos": 房本备件 仅 "已上传房本照片"/"未上传房本照片",
  "property_type": 交易权属 仅 "商品房"/"已购公房"/"经适房"/"共有产权"/"商住两用",
  "ceiling_height": 楼层高度/层高(米),
  "heating_type": 供暖方式 仅 "集中供暖"/"地暖"/"自采暖"/"无",
  "property_use": 仅 "普通住宅"/"公寓"/"商住两用"/"别墅"/"办公",
  "ownership": 仅 "非共有"/"共有"/"部分共有",
  "north_south"/"has_elevator": 满足填 1 否则 0,
  "elevator_ratio": 如 "三梯八户"→"3T8",
  "subway_distance": 距地铁米数
}

【房间明细必须逐间给全 4 项：房间名/面积/朝向/窗型】
- room_detail 里每一间都要写满"名称 面积平米 朝向 窗型"，不允许只写名称和面积。
- 朝向：按图上指北针(N 箭头)与房间在图中的位置推断该间外窗/阳台朝哪个方向，如 南/北/东南/西南/东北/西北；正中间无外窗的房间(过道、玄关)填 无。
- 窗型：整面大面积玻璃、连到地面或顶棚的画法=落地窗；外墙上的普通窗=普通窗；飘窗=飘窗；无外窗的房间=无窗。
- 朝向与窗型是图面几何可推导的客观事实，必须依据指北针和外窗位置推断，不要因为图上没印这几个字就留空或填 null。
- 图上若同时给了整户朝向(如"房屋朝向 南 北")，可作为各间朝向的参考。
- 房间名照抄图上标注(卧室A/过道B/阳台A 等)，没有标名的房间可按功能命名。"""


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
    _num("interior_area", 20, 800)
    _num("price", 30, 30000)
    _num("layout_rooms", 0, 10)
    _num("layout_halls", 0, 8)
    _num("layout_kitchens", 0, 5)
    _num("layout_baths", 0, 5)
    _num("floor", 1, 80)
    _num("total_floors", 1, 99)
    _num("subway_distance", 10, 5000)
    _num("master_area", 4, 100)
    _num("living_area", 8, 200)
    _num("second_bedroom_area", 4, 100)
    _num("study_area", 3, 40)
    _num("entrance_area", 1, 15)
    _num("last_trade_year", 1980, 2026)
    _num("ceiling_height", 2.0, 5.0)
    # 挂牌时间：只收 YYYY-MM-DD
    ld = str(raw.get("list_date") or "").strip()
    ml = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", ld)
    if ml and 2000 <= int(ml.group(1)) <= 2026:
        fields["list_date"] = "%s-%02d-%02d" % (ml.group(1), int(ml.group(2)), int(ml.group(3)))
    # 房本备件
    cp = str(raw.get("cert_photos") or "").strip()
    if cp in {"已上传房本照片", "未上传房本照片"}:
        fields["cert_photos"] = cp
    # 楼层统一为 "n/m"（所在/总楼层）；只在所在层明确写出时才有值，未知楼层不填
    ff = str(raw.get("floor_frac") or "").strip()
    mf = re.match(r"^(\d{1,2})\s*[/-]\s*(\d{1,3})$", ff)
    if mf and 1 <= int(mf.group(1)) <= int(mf.group(2)) <= 99:
        fields["floor_frac"] = f"{int(mf.group(1))}/{int(mf.group(2))}"
    else:
        # 兼容旧输出：floor+total_floors 都有才组合；只有总楼层=楼层未知，不填
        _num("floor", 1, 80)
        _num("total_floors", 1, 99)
        if (fields.get("floor") and fields.get("total_floors")
                and fields["floor"] <= fields["total_floors"]):
            fields["floor_frac"] = f"{fields['floor']}/{fields['total_floors']}"
        fields.pop("floor", None)
        fields.pop("total_floors", None)
    # 套内面积不可能大于建筑面积，异常时丢弃
    if fields.get("interior_area") and fields.get("area_size") and fields["interior_area"] > fields["area_size"]:
        fields.pop("interior_area")

    for key, allowed in (("orientation", _ORIENTATIONS), ("decoration", _DECOR_SET),
                         ("layout_structure", {"平层", "错层", "跃层", "复式"}),
                         ("building_type", {"塔楼", "板楼", "塔板结合"}),
                         ("property_use", {"普通住宅", "公寓", "商住两用", "别墅", "办公"}),
                         ("ownership", {"非共有", "共有", "部分共有"}),
                         ("property_type", {"商品房", "已购公房", "经适房", "共有产权", "商住两用"}),
                         ("heating_type", {"集中供暖", "地暖", "自采暖", "无"})):
        v = str(raw.get(key) or "").strip()
        if v in allowed:
            fields[key] = v

    # 建筑结构：容忍"钢混结构/砖混结构/混合结构"带后缀输出
    bs = str(raw.get("building_structure") or "").strip().replace("结构", "")
    if bs in {"钢混", "砖混", "混合", "钢结构", "砖木"}:
        fields["building_structure"] = bs

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

    # 房间明细：自由文本，须含数字（面积），限长防注入超长内容
    rd = re.sub(r"\s+", " ", str(raw.get("room_detail") or "")).strip()
    if rd and re.search(r"\d", rd) and len(rd) <= 400:
        fields["room_detail"] = rd

    # 满五唯一/有抵押不在自动识别范围（交易状态影响税费，须用户自行勾选）
    # 勾选类字段只回传 1（是）；0（否）一律忽略——未勾选就不该标蓝误导用户
    for key in ("north_south", "has_elevator"):
        if str(raw.get(key) or "") == "1":
            fields[key] = "1"
    return fields


def _vl_call_once(client, model, uri, timeout=60, max_tokens=1800, extra_body=None):
    """单张截图调一次多模态接口，返回 (文本输出, finish_reason)。"""
    content = [{"type": "text", "text": VL_PROMPT},
               {"type": "image_url", "image_url": {"url": uri}}]
    kwargs = dict(model=model, temperature=0, max_tokens=max_tokens,
                  timeout=timeout,  # 单张请求上限（秒），到点即断不空等
                  messages=[{"role": "user", "content": content}])
    if extra_body:
        kwargs["extra_body"] = extra_body
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    out = getattr(getattr(choice, "message", None), "content", None) or ""
    if isinstance(out, list):  # 兼容把 content 拆成多段返回的网关
        out = "".join(p.get("text", "") for p in out if isinstance(p, dict))
    finish = str(getattr(choice, "finish_reason", "") or "")
    return out, finish


def _extract_json_obj(out):
    """从模型输出中稳健提取 JSON 对象；失败返回 None。"""
    text = str(out or "").strip()
    if text.startswith("```"):  # 去掉 markdown 围栏
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    import json as _json
    try:
        data = _json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _vl_build_clients():
    """按优先级组装视觉服务客户端：DeepSeek（主力，关思考提速）→ MiMo（备用）。"""
    from openai import OpenAI
    clients = []
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if ds_key:
        clients.append(("DeepSeek",
                        OpenAI(api_key=ds_key,
                               base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()),
                        os.environ.get("DEEPSEEK_VL_MODEL", "deepseek-flash").strip(),
                        8192,
                        {"thinking": {"type": "disabled"}}))
    mimo_key = os.environ.get("MIMO_API_KEY", "").strip()
    if mimo_key:
        clients.append(("MiMo",
                        OpenAI(api_key=mimo_key,
                               base_url=os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip()),
                        os.environ.get("MIMO_VL_MODEL", "mimo-v2.6-flash").strip(),
                        1800,
                        None))
    return clients


def vision_available():
    """截图 AI 识别是否可用（仅判断服务器是否配置了视觉密钥，绝不暴露密钥本身）。

    供前端优雅降级：访客无需填写任何密钥；无密钥时前端提示「手动填写 / 免费文字解析」。
    """
    try:
        return len(_vl_build_clients()) > 0
    except Exception:  # noqa: BLE001
        return False


def _vl_call_with_fallback(clients, uri, timeout=60):
    """单张截图按 主力→备用 顺序尝试；返回 (输出, finish, 服务商)。全部失败抛最后异常。"""
    last_err = None
    for name, client, model, max_tokens, extra_body in clients:
        try:
            out, finish = _vl_call_once(client, model, uri, timeout, max_tokens, extra_body)
            if str(out or "").strip():
                return out, finish, name
            last_err = RuntimeError("%s 返回空输出" % name)
            logger.warning("VL %s 空输出(finish=%s)，切换备用", name, finish)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("VL %s 调用失败: %s %s，切换备用",
                           name, type(e).__name__, str(e)[:120])
    raise last_err or RuntimeError("无可用视觉服务")


def parse_images_with_vl(data_uris):
    """调用视觉大模型解析截图（DeepSeek 主力，MiMo 备用）：多张并行、每张独立调用。

    恒返回 4 元组 (fields, error, warning, floor_plan_index)；
    部分图片成功即返回已提取字段，失败项作为 warning 提示；全部无结果才返回 error。
    """
    if not data_uris:
        return {}, None, None, None
    try:
        clients = _vl_build_clients()
    except Exception as e:  # noqa: BLE001
        logger.warning("视觉客户端初始化失败: %s %s", type(e).__name__, str(e)[:120])
        return {}, ("AI 视觉服务初始化失败（%s），请稍后重试或改用文字粘贴" % type(e).__name__), None, None
    if not clients:
        return {}, ("截图 AI 识别未开启：站长尚未在服务器配置视觉密钥（访客无需填写任何密钥）。"
                    "您可直接手动填写，或用「免费文字解析」粘贴房源文字自动提取，同样精准。"), None, None
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        outcomes = [None] * len(data_uris)  # (out, finish, provider) / Exception
        with ThreadPoolExecutor(max_workers=min(3, len(data_uris))) as pool:
            futures = {pool.submit(_vl_call_with_fallback, clients, uri, 60): i
                       for i, uri in enumerate(data_uris)}
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    outcomes[i] = fut.result()
                except Exception as e:  # noqa: BLE001
                    outcomes[i] = e

        fields, errors, fp_index = {}, [], None
        for i, outcome in enumerate(outcomes):
            idx = i + 1
            if isinstance(outcome, Exception):
                logger.warning("VL 第%d张调用失败: %s %s",
                               idx, type(outcome).__name__, str(outcome)[:120])
                errors.append(f"第{idx}张调用失败（{type(outcome).__name__}）")
                continue
            out, finish, provider = outcome
            data = _extract_json_obj(out)
            if data is None:
                # 失败必留痕：记 finish_reason 与原始输出头部，便于定位（不含图片数据）
                head = re.sub(r"\s+", " ", str(out))[:120]
                logger.warning("VL 第%d张(%s)无JSON: finish=%s head=%r", idx, provider, finish, head)
                if finish == "content_filter":
                    errors.append(f"第{idx}张被内容安全拦截")
                elif finish == "length":
                    errors.append(f"第{idx}张输出被截断")
                else:
                    errors.append(f"第{idx}张未识别出有效数据")
                continue
            per_image = _normalize_vl_output(data)
            for k, v in per_image.items():
                if v not in (None, "") and not fields.get(k):
                    fields[k] = v
            if fp_index is None and per_image.get("room_detail"):
                fp_index = i  # 产出房间明细的那张截图即户型图
        if not fields:
            msg = "AI 未返回有效结果，请重试或改用文字粘贴"
            if errors:
                msg += "（" + "；".join(errors) + "）"
            return {}, msg, None, None
        # 部分成功：字段照常返回，未完成项作为 warning 提示给用户
        return fields, None, ("；".join(errors) if errors else None), fp_index
    except Exception as e:  # noqa: BLE001
        logger.warning("VL 解析失败: %s %s", type(e).__name__, str(e)[:120])
        return {}, ("AI 解析调用失败，请稍后重试或改用文字粘贴"
                    f"（{type(e).__name__}）"), None, None
