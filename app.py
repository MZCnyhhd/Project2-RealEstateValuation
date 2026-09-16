import os
import logging
from math import ceil
from flask import Flask, render_template, request, redirect, session, jsonify, url_for

from repository import get_repository, get_order_store
from mortgage import calculate_mortgage
from llm_agent import build_agent_reply
from beijing_policy import apply_beijing_policy
from valuation import evaluate_listing, evaluate_user_input, format_layout
import payments

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # 输出到终端
    ]
)
logger = logging.getLogger(__name__)

# 后台管理密码（请在 Render 环境变量中设置 ADMIN_PASSWORD，本地默认仅用于测试）
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change_me_admin")
if ADMIN_PASSWORD == "change_me_admin":
    logger.warning("ADMIN_PASSWORD 使用默认弱口令，请在生产环境通过环境变量设置强密码")

def load_listings(filename="mock_listings.json"):
    repo = get_repository(filename=filename)
    return repo.list_listings()


def _favorites_set():
    fav = session.get("favorites", [])
    if not isinstance(fav, list):
        fav = []
    return set([x for x in fav if isinstance(x, str)])


def _compare_set():
    cmp_ = session.get("compare", [])
    if not isinstance(cmp_, list):
        cmp_ = []
    return set([x for x in cmp_ if isinstance(x, str)])


def _save_set(key, value_set, limit=None):
    ids = sorted(list(value_set))
    if limit is not None:
        ids = ids[:limit]
    session[key] = ids

def search_listings(listings, min_price=None, max_price=None, min_area=None, max_area=None, q=None, tags=None):
    """根据多个条件筛选房源"""
    results = listings

    if min_price is not None:
        results = [listing for listing in results if listing['price'] >= min_price]
    if max_price is not None:
        results = [listing for listing in results if listing['price'] <= max_price]
    if min_area is not None:
        results = [listing for listing in results if listing['area'] >= min_area]
    if max_area is not None:
        results = [listing for listing in results if listing['area'] <= max_area]

    if q:
        q_lower = q.strip().lower()
        if q_lower:
            def _match(listing):
                haystack = " ".join(
                    str(listing.get(k, ""))
                    for k in ("title", "community", "address", "description")
                ).lower()
                return q_lower in haystack

            results = [listing for listing in results if _match(listing)]

    if tags:
        tag_set = set([t for t in tags if t])
        if tag_set:
            results = [
                listing
                for listing in results
                if tag_set.issubset(set(listing.get("tags", [])))
            ]

    return results

@app.route("/")
def landing():
    """产品介绍页 - 首页"""
    return render_template("landing.html")


@app.route("/listings")
def index():
    """房源列表和搜索页"""
    logger.info(f"房源列表访问 - 查询参数: {request.args}")
    all_listings = load_listings()

    # 从URL参数获取搜索条件和排序方式
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    min_area = request.args.get('min_area', type=float)
    max_area = request.args.get('max_area', type=float)
    sort_by = request.args.get('sort_by', 'default')
    q = request.args.get('q', type=str, default='')
    selected_tags = request.args.getlist('tags')
    page = request.args.get('page', type=int, default=1)
    page_size = request.args.get('page_size', type=int, default=12)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 12
    if page_size > 48:
        page_size = 48

    # 筛选房源
    search_results = search_listings(all_listings, min_price, max_price, min_area, max_area, q=q, tags=selected_tags)

    # 排序结果
    if sort_by == 'price_asc':
        search_results.sort(key=lambda x: x['price'])
    elif sort_by == 'price_desc':
        search_results.sort(key=lambda x: x['price'], reverse=True)
    elif sort_by == 'area_asc':
        search_results.sort(key=lambda x: x['area'])
    elif sort_by == 'area_desc':
        search_results.sort(key=lambda x: x['area'], reverse=True)

    url_params = request.args.to_dict()
    url_params_multi = request.args.to_dict(flat=False)

    base_params = dict(url_params_multi)
    base_params.pop("sort_by", None)
    base_params.pop("page", None)
    sort_urls = {
        "price_asc": app.url_for("index", **base_params, sort_by="price_asc"),
        "price_desc": app.url_for("index", **base_params, sort_by="price_desc"),
        "area_asc": app.url_for("index", **base_params, sort_by="area_asc"),
        "area_desc": app.url_for("index", **base_params, sort_by="area_desc"),
    }

    total_results = len(search_results)
    total_pages = max(1, int(ceil(total_results / float(page_size))))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    end = start + page_size
    paged_listings = search_results[start:end]

    page_urls = {"prev": None, "next": None, "pages": []}

    page_base_params = dict(url_params_multi)
    page_base_params.pop("page", None)
    if page > 1:
        page_urls["prev"] = app.url_for("index", **page_base_params, page=page - 1)
    if page < total_pages:
        page_urls["next"] = app.url_for("index", **page_base_params, page=page + 1)

    window = 2
    candidates = {1, total_pages}
    for p in range(max(1, page - window), min(total_pages, page + window) + 1):
        candidates.add(p)
    ordered = sorted(candidates)
    last = None
    for p in ordered:
        if last is not None and p - last > 1:
            page_urls["pages"].append({"num": None, "url": None, "active": False, "label": "..."})
        page_urls["pages"].append(
            {
                "num": p,
                "url": app.url_for("index", **page_base_params, page=p),
                "active": p == page,
                "label": str(p),
            }
        )
        last = p

    available_tags = sorted({t for item in all_listings for t in item.get("tags", [])})
    favorites = _favorites_set()
    compare = _compare_set()

    return render_template(
        "index.html",
        listings=paged_listings,
        total_results=total_results,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        url_params=url_params,
        sort_urls=sort_urls,
        page_urls=page_urls,
        q=q,
        available_tags=available_tags,
        selected_tags=selected_tags,
        favorites=favorites,
        compare=compare,
    )


@app.route("/favorite/<listing_id>")
def toggle_favorite(listing_id):
    fav = _favorites_set()
    if listing_id in fav:
        fav.remove(listing_id)
    else:
        fav.add(listing_id)
    _save_set("favorites", fav)
    next_url = request.args.get("next") or app.url_for("index")
    return redirect(next_url)


@app.route("/compare/<listing_id>")
def toggle_compare(listing_id):
    cmp_ = _compare_set()
    if listing_id in cmp_:
        cmp_.remove(listing_id)
    else:
        cmp_.add(listing_id)
    _save_set("compare", cmp_, limit=5)
    next_url = request.args.get("next") or app.url_for("index")
    return redirect(next_url)


@app.route("/favorites")
def favorites_page():
    all_listings = load_listings()
    fav = _favorites_set()
    listings = [x for x in all_listings if x.get("id") in fav]
    return render_template("favorites.html", listings=listings, favorites=fav, compare=_compare_set())


@app.route("/compare")
def compare_page():
    all_listings = load_listings()
    cmp_ = _compare_set()
    listings = [x for x in all_listings if x.get("id") in cmp_]
    return render_template("compare.html", listings=listings, favorites=_favorites_set(), compare=cmp_)

@app.route("/listing/<listing_id>")
def listing_detail(listing_id):
    """房源详情页"""
    all_listings = load_listings()
    # 查找与传入ID匹配的房源
    listing = next((item for item in all_listings if item["id"] == listing_id), None)
    
    if listing:
        valuation = evaluate_listing(listing, all_listings=all_listings)
        return render_template(
            "listing_detail.html",
            listing=listing,
            valuation=valuation,
            favorites=_favorites_set(),
            compare=_compare_set(),
        )
    else:
        return render_template("404.html"), 404


@app.route("/calculator", methods=["GET", "POST"])
def calculator():
    defaults = {
        "total_price_wan": 600.0,
        "down_payment_ratio_pct": 35.0,
        "years": 30,
        "annual_rate_pct": 3.1,
        "repayment_method": "equal_payment",
        "monthly_income": 50000.0,
        "monthly_debt": 0.0,
        "purchase_type": "first_home",
        "housing_type": "normal",
        "loan_type": "commercial",
    }
    form_data = dict(defaults)
    result = None
    error = None
    policy_meta = None

    if request.method == "POST":
        try:
            form_data["total_price_wan"] = request.form.get("total_price_wan", type=float, default=defaults["total_price_wan"])
            form_data["down_payment_ratio_pct"] = request.form.get("down_payment_ratio_pct", type=float, default=defaults["down_payment_ratio_pct"])
            form_data["years"] = request.form.get("years", type=int, default=defaults["years"])
            form_data["annual_rate_pct"] = request.form.get("annual_rate_pct", type=float, default=defaults["annual_rate_pct"])
            form_data["repayment_method"] = request.form.get("repayment_method", default=defaults["repayment_method"])
            form_data["monthly_income"] = request.form.get("monthly_income", type=float, default=defaults["monthly_income"])
            form_data["monthly_debt"] = request.form.get("monthly_debt", type=float, default=defaults["monthly_debt"])
            form_data["purchase_type"] = request.form.get("purchase_type", default=defaults["purchase_type"])
            form_data["housing_type"] = request.form.get("housing_type", default=defaults["housing_type"])
            form_data["loan_type"] = request.form.get("loan_type", default=defaults["loan_type"])

            policy_meta = apply_beijing_policy(
                down_payment_ratio_pct=form_data["down_payment_ratio_pct"],
                annual_rate_pct=form_data["annual_rate_pct"],
                purchase_type=form_data["purchase_type"],
                housing_type=form_data["housing_type"],
                loan_type=form_data["loan_type"],
            )

            calc_payload = {
                "total_price_wan": form_data["total_price_wan"],
                "down_payment_ratio_pct": policy_meta["effective_down_payment_ratio_pct"],
                "years": form_data["years"],
                "annual_rate_pct": policy_meta["effective_annual_rate_pct"],
                "repayment_method": form_data["repayment_method"],
                "monthly_income": form_data["monthly_income"],
                "monthly_debt": form_data["monthly_debt"],
            }
            result = calculate_mortgage(**calc_payload)
        except ValueError as exc:
            error = str(exc)

    return render_template(
        "calculator.html",
        form_data=form_data,
        result=result,
        error=error,
        policy_meta=policy_meta,
    )


@app.route("/agent/chat", methods=["POST"])
def agent_chat():
    payload = request.get_json(silent=True) or {}
    message = payload.get("message", "")
    if not isinstance(message, str) or not message.strip():
        logger.warning("Agent聊天请求 - 消息为空")
        return jsonify({"ok": False, "error": "message 不能为空"}), 400

    try:
        logger.info(f"Agent聊天请求 - 消息: {message[:100]}...")
        reply, calc = build_agent_reply(message)
        logger.info(f"Agent聊天响应 - 成功")
        return jsonify({"ok": True, "reply": reply, "calculation": calc})
    except ValueError as exc:
        logger.error(f"Agent聊天错误 - {str(exc)}")
        return jsonify({"ok": False, "error": str(exc)}), 400


# 全息估值定价（元）—— 标准报告默认价，同时作为兜底
VALUATION_PRICE = 9.9


def _env_price(env_key, default):
    """套餐价格可由环境变量覆盖，便于不改代码调价。"""
    try:
        return round(float(os.environ.get(env_key, default)), 2)
    except (TypeError, ValueError):
        return float(default)


# 套餐定义。kind=report 支付后即时解锁报告；kind=service 支付后转人工交付
PLANS = {
    "standard": {
        "key": "standard",
        "name": "全息估值报告 · 完整版",
        "desc": "7层 × 182维度逐项明细 · 每项折合万元 · 可打印报告",
        "price": _env_price("PRICE_STANDARD", VALUATION_PRICE),
        "kind": "report",
        "fulfill": "在线即时交付",
    },
    "pro": {
        "key": "pro",
        "name": "专业版报告（含实勘）",
        "desc": "含完整版全部 · 周边实勘补充数据 · 市场对比分析 · 定价策略建议",
        "price": _env_price("PRICE_PRO", 49.0),
        "kind": "service",
        "fulfill": "人工安排实勘（1-3 个工作日）",
    },
    "pro_plus": {
        "key": "pro_plus",
        "name": "专业版报告 · 深度实勘",
        "desc": "含专业版全部 · 专人上门实勘 · 一对一解读",
        "price": _env_price("PRICE_PRO_PLUS", 99.0),
        "kind": "service",
        "fulfill": "专人上门实勘（1-3 个工作日）",
    },
}
DEFAULT_PLAN_KEY = "standard"

# 展示用价格标签（去掉多余的 .0，如 49.0 → 49）
for _p in PLANS.values():
    _p["price_label"] = f"{_p['price']:g}"


def get_plan(key):
    """按 key 取套餐；非法 key 一律回退默认套餐（价格以服务端为准，防止前端篡改）。"""
    return PLANS.get((key or "").strip()) or PLANS[DEFAULT_PLAN_KEY]


def _new_order_no():
    """生成估值订单号，形如 HV20260909A1B2C3"""
    from datetime import datetime
    import random
    stamp = datetime.now().strftime("%Y%m%d")
    suffix = "".join(random.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(6))
    return f"HV{stamp}{suffix}"


@app.route("/valuate")
def valuate_form():
    """全息估值 - 用户录入房源信息表单"""
    return render_template("valuate_form.html", price=VALUATION_PRICE)


def _normalize_valuate_form():
    """提取并归一化估值表单字段"""
    form_data = {}
    for key in request.form:
        val = request.form.get(key, "").strip()
        if val:
            form_data[key] = val

    checkbox_fields = ["north_south", "has_elevator", "ped_car_split", "five_year_only",
                       "has_mortgage", "has_lease", "urgent_sell", "is_haunted",
                       "has_leak", "door_toilet", "beam_press", "squareness",
                       "dry_wet_sep", "bright_bath", "bright_kitchen",
                       "has_central_ac", "has_fresh_air"]
    for field in checkbox_fields:
        if field not in form_data:
            form_data[field] = ""
    return form_data


# 用户估值房源回流到 Demo 房源时使用的配图（取自现有 Demo 数据，保证可加载）
_USER_LISTING_IMAGES = [
    "https://images.unsplash.com/photo-1630699293388-0938c397106a?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDV8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1684928365167-e91916573122?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDd8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1562821696-c68d007f943b?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDl8&ixlib=rb-4.1.0&q=80&w=1080",
]


def _build_listing_from_form(form_data, order_no):
    """把用户估值表单转换成一条 Demo 房源记录；缺面积或报价时返回 None。"""
    def _num(key):
        try:
            val = float(str(form_data.get(key, "")).strip())
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None

    def _flag(key):
        return str(form_data.get(key, "")).strip() not in ("", "0", "None", "False", "false")

    area = _num("area_size")
    price = _num("price")
    if area is None or price is None:
        return None

    layout = format_layout(form_data)

    # 标签按「搜索价值」排序：越靠前越能帮买家筛到这套房，超出的截断
    deco = (form_data.get("decoration") or "").strip()
    subway = _num("subway_distance")
    school = (form_data.get("school_level") or "").strip()
    view = (form_data.get("view_type") or "").strip()

    tags = ["新上"]
    if subway is not None and subway <= 800:
        tags.append("近地铁")
    if school and school not in ("无", "普通", "暂无"):
        tags.append("学区房")
    if _flag("north_south"):
        tags.append("南北通透")
    if deco:
        tags.append(deco)
    if _flag("five_year_only"):
        tags.append("满五唯一")
    if _flag("ped_car_split"):
        tags.append("人车分流")
    if _flag("has_elevator"):
        tags.append("电梯房")
    if _flag("urgent_sell"):
        tags.append("急售")
    if view and view not in ("无", "普通"):
        tags.append(view)
    if _flag("is_haunted"):
        tags.append("历史印记")
    if area >= 180:
        tags.append("大平层")

    seen, clean_tags = set(), []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            clean_tags.append(t)
    clean_tags = clean_tags[:6]

    community = (form_data.get("community") or "未知小区").strip()
    address = (form_data.get("address") or community).strip()

    highlight = "、".join(clean_tags[1:3]) or "业主自荐"
    title = f"{community} {layout}，{highlight}"

    bits = [f"建筑面积约{area:g}㎡，{layout}"]
    if form_data.get("floor"):
        bits.append(f"位于{form_data['floor']}层")
    if form_data.get("orientation"):
        bits.append(f"{form_data['orientation']}朝向")
    if deco:
        bits.append(deco)
    bits.append("房源信息由用户估值自动录入。")

    idx = sum(ord(c) for c in order_no) % len(_USER_LISTING_IMAGES)
    return {
        "id": f"U-{order_no}",
        "title": title,
        "community": community,
        "address": address,
        "layout": layout,
        "area": area,
        "price": price,
        "tags": clean_tags,
        "description": "，".join(bits),
        "image_url": _USER_LISTING_IMAGES[idx],
        "latitude": None,
        "longitude": None,
    }


@app.route("/valuate/report", methods=["POST"])
def valuate_report():
    """全息估值 - 提交房源信息，生成待支付订单后跳转收银台"""
    form_data = _normalize_valuate_form()

    if not form_data.get("community") or not form_data.get("area_size") or not form_data.get("price"):
        return render_template("valuate_form.html", price=VALUATION_PRICE,
                               error="请填写必填字段：小区名称、面积、报价")

    default_plan = get_plan(DEFAULT_PLAN_KEY)
    order = {
        "order_no": _new_order_no(),
        "form_data": form_data,
        "community": form_data.get("community", ""),
        "area_size": form_data.get("area_size", ""),
        "listing_price": form_data.get("price", ""),
        "plan": default_plan["key"],
        "plan_name": default_plan["name"],
        "plan_kind": default_plan["kind"],
        "amount": default_plan["price"],
        "paid": False,
        "pay_method": None,
        "wx_code_url": None,
        "ali_qr_code": None,
    }
    session["pending_valuation"] = order
    get_order_store().save(order)
    # 上一条订单的支付状态作废，避免复用旧支付凭证
    session.pop("valuation_paid_no", None)

    logger.info(f"估值订单创建 - 单号: {order['order_no']}, 小区: {order['community']}, "
                f"面积: {order['area_size']}, 报价: {order['listing_price']}, "
                f"套餐: {default_plan['name']}, 金额: ¥{default_plan['price']}")
    return redirect("/valuate/pay")


@app.route("/valuate/pay")
def valuate_pay():
    """收银台 - 支持多套餐（标准报告 / 专业版实勘），未支付时可切换套餐"""
    order = session.get("pending_valuation")
    if not order:
        return redirect("/valuate")

    store = get_order_store()
    plan = get_plan(request.args.get("plan") or order.get("plan"))

    # 已支付的「报告类」订单直接进报告页；「服务类」订单留在本页展示交付状态
    if (order.get("paid")
            and session.get("valuation_paid_no") == order["order_no"]
            and plan["kind"] == "report"):
        return redirect("/valuate/result")

    # 套餐变更：金额变了则作废旧支付单，按新金额重新下单
    if not order.get("paid") and (order.get("plan") != plan["key"] or order.get("amount") != plan["price"]):
        order["plan"] = plan["key"]
        order["plan_name"] = plan["name"]
        order["plan_kind"] = plan["kind"]
        order["amount"] = plan["price"]
        order["wx_code_url"] = None
        session["pending_valuation"] = order
        store.update(order["order_no"], plan=plan["key"], plan_name=plan["name"],
                     plan_kind=plan["kind"], amount=plan["price"], wx_code_url=None)

    # 真实支付：若已配置微信官方 API，则按套餐金额在服务端创建支付单并拿到二维码
    if not order.get("wx_code_url"):
        if payments.WX_CONFIGURED:
            desc = f"{plan['name']}-{order['community']}"
            order["wx_code_url"] = payments.create_wechat_native(
                order["order_no"], int(round(plan["price"] * 100)), desc,
                url_for("valuate_pay_notify_wechat", _external=True),
            )
            store.update(order["order_no"], wx_code_url=order.get("wx_code_url"))

    return render_template(
        "valuate_pay.html", order=order, plan=plan, plans=list(PLANS.values()),
        price=plan["price"],
        wx_configured=payments.WX_CONFIGURED,
        official=payments.WX_CONFIGURED,
        done=bool(request.args.get("done")) or (bool(order.get("paid")) and plan["kind"] == "service"),
    )


@app.route("/valuate/pay/confirm", methods=["POST"])
def valuate_pay_confirm():
    """确认支付：用户点「支付成功」后按套餐类型交付。

    * kind=report（标准报告）：直接解锁并生成完整估值报告。
    * kind=service（专业版实勘）：不生成报告，转人工交付，提示客服将联系安排实勘。
    * 官方支付模式（已配置微信 API）：以回调验签为准，未到账则回收银台等待。
    """
    order = session.get("pending_valuation")
    if not order:
        return redirect("/valuate")

    pay_method = request.form.get("pay_method", "wechat")
    if pay_method not in ("wechat", "alipay"):
        pay_method = "wechat"

    # 套餐以服务端定义为准：前端只提交 key，价格不可被篡改
    plan = get_plan(request.form.get("plan") or order.get("plan"))
    order["plan"] = plan["key"]
    order["plan_name"] = plan["name"]
    order["plan_kind"] = plan["kind"]
    order["amount"] = plan["price"]

    store = get_order_store()
    official = payments.WX_CONFIGURED or payments.ALI_CONFIGURED

    from datetime import datetime

    # 官方支付模式：解锁取决于回调验签
    if official:
        real_paid = store.get(order["order_no"]) or {}
        if not real_paid.get("paid"):
            session["pending_valuation"] = order
            return redirect("/valuate/pay?wait=1")
        order["paid"] = True
        order["pay_method"] = real_paid.get("pay_method") or pay_method
        order["paid_at"] = real_paid.get("paid_at")
    else:
        # 个人收款码模式：用户自主确认已付款
        paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order["paid"] = True
        order["pay_method"] = pay_method
        order["paid_at"] = paid_at

    order["review_status"] = "confirmed"
    session["pending_valuation"] = order
    session["valuation_paid_no"] = order["order_no"]
    store.update(order["order_no"], paid=True,
                 pay_method=order["pay_method"], paid_at=order["paid_at"],
                 plan=plan["key"], plan_name=plan["name"],
                 plan_kind=plan["kind"], amount=plan["price"],
                 review_status="confirmed")
    logger.info(f"估值订单已支付 - 单号: {order['order_no']}, 套餐: {plan['name']}, "
                f"金额: ¥{plan['price']}, 渠道: {pay_method}")

    # 服务类套餐（专业版实勘）转人工交付，其余即时解锁报告
    if plan["kind"] == "service":
        return redirect("/valuate/pay?done=1")
    return redirect("/valuate/result")


@app.route("/valuate/pay/status")
def valuate_pay_status():
    """前端轮询：返回订单支付状态。"""
    order_no = request.args.get("order_no") or (session.get("pending_valuation") or {}).get("order_no")
    if not order_no:
        return jsonify({"paid": False})
    order = get_order_store().get(order_no) or {}
    return jsonify({"paid": bool(order.get("paid")), "method": order.get("pay_method")})


@app.route("/valuate/pay/notify/wechat", methods=["POST"])
def valuate_pay_notify_wechat():
    """微信支付异步回调：验签 + 标记订单已支付。"""
    body = request.get_data(as_text=True)
    out_trade_no, paid = payments.verify_wechat_notify(dict(request.headers), body)
    if out_trade_no and paid:
        from datetime import datetime
        get_order_store().update(
            out_trade_no, paid=True, pay_method="wechat",
            paid_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        logger.info("微信支付到账 - 单号: %s", out_trade_no)
        return jsonify({"code": "SUCCESS", "message": "成功"})
    return jsonify({"code": "FAIL", "message": "验签失败"}), 400


@app.route("/valuate/pay/notify/alipay", methods=["POST"])
def valuate_pay_notify_alipay():
    """支付宝异步回调：验签 + 标记订单已支付。"""
    form = request.form.to_dict()
    out_trade_no, paid = payments.verify_alipay_notify(form)
    if out_trade_no and paid:
        from datetime import datetime
        get_order_store().update(
            out_trade_no, paid=True, pay_method="alipay",
            paid_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        logger.info("支付宝支付到账 - 单号: %s", out_trade_no)
        return "success"
    return "failure", 400


@app.route("/valuate/result")
def valuate_result():
    """完整估值报告 - 仅支付成功后可访问（以服务端订单存储为权威来源）"""
    order_no = (session.get("pending_valuation") or {}).get("order_no") or session.get("valuation_paid_no")
    order = get_order_store().get(order_no) if order_no else None
    if not order:
        return redirect("/valuate")
    if not order.get("paid"):
        return redirect("/valuate/pay")

    session["pending_valuation"] = order
    session["valuation_paid_no"] = order["order_no"]
    valuation = evaluate_user_input(order["form_data"])

    # 报告生成后，把该房产回流到 Demo 房源库（同一订单仅入库一次）
    if not order.get("listing_id"):
        listing = _build_listing_from_form(order["form_data"], order["order_no"])
        if listing:
            try:
                get_repository().add_listing(listing)
                order["listing_id"] = listing["id"]
                get_order_store().update(order["order_no"], listing_id=listing["id"])
                logger.info("房产已加入 Demo 房源 - 单号: %s, 房源号: %s",
                            order["order_no"], listing["id"])
            except Exception as exc:
                logger.error("加入 Demo 房源失败 - 单号: %s, 错误: %s", order["order_no"], exc)

    return render_template("valuate_report.html", valuation=valuation, order=order, is_paid=True,
                           layout_text=format_layout(order["form_data"]))


@app.route("/valuate/unlock")
def valuate_unlock():
    """旧「免费解锁」入口已废弃，统一走收银台"""
    return redirect("/valuate/pay")


# ---------------------------------------------------------------------------
# 后台：人工核验（手动确认收款）
# ---------------------------------------------------------------------------
def _admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/admin/login")
        return view(*args, **kwargs)

    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/admin/orders")
        return render_template("admin_login.html", error="密码错误，请重试")
    return render_template("admin_login.html", error=None)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin/login")


@app.route("/admin/orders")
@_admin_required
def admin_orders():
    orders = list(get_order_store().all().values())
    orders.sort(key=lambda o: o.get("submitted_at") or o.get("order_no"), reverse=True)
    return render_template("admin_orders.html", orders=orders)


@app.route("/admin/orders/<order_no>/confirm", methods=["POST"])
@_admin_required
def admin_confirm(order_no):
    from datetime import datetime
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    get_order_store().update(order_no, paid=True, review_status="confirmed", paid_at=paid_at)
    logger.info("商家确认收款 - 单号: %s", order_no)
    return redirect("/admin/orders")


@app.route("/admin/orders/<order_no>/reject", methods=["POST"])
@_admin_required
def admin_reject(order_no):
    get_order_store().update(order_no, review_status="rejected")
    logger.info("商家驳回订单 - 单号: %s", order_no)
    return redirect("/admin/orders")

if __name__ == "__main__":
    logger.info("RealEstate Flask应用启动")
    app.run(debug=True)