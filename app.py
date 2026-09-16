import os
import logging
from math import ceil
from flask import Flask, render_template, request, redirect, session, jsonify

from repository import get_repository, get_order_store
from mortgage import calculate_mortgage
from llm_agent import build_agent_reply
from beijing_policy import apply_beijing_policy
from valuation import evaluate_listing, evaluate_user_input
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


# 全息估值定价（元）
VALUATION_PRICE = 9.9


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
                       "dry_wet_sep", "bright_bath", "has_central_ac", "has_fresh_air"]
    for field in checkbox_fields:
        if field not in form_data:
            form_data[field] = ""
    return form_data


@app.route("/valuate/report", methods=["POST"])
def valuate_report():
    """全息估值 - 提交房源信息，先生成免费体验版报告（完整版可解锁）"""
    form_data = _normalize_valuate_form()

    if not form_data.get("community") or not form_data.get("area_size") or not form_data.get("price"):
        return render_template("valuate_form.html", price=VALUATION_PRICE,
                               error="请填写必填字段：小区名称、面积、报价")

    order = {
        "order_no": _new_order_no(),
        "form_data": form_data,
        "community": form_data.get("community", ""),
        "area_size": form_data.get("area_size", ""),
        "listing_price": form_data.get("price", ""),
        "amount": VALUATION_PRICE,
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
                f"面积: {order['area_size']}, 报价: {order['listing_price']}, 金额: ¥{VALUATION_PRICE}")
    return redirect("/valuate/result")


@app.route("/valuate/pay")
def valuate_pay():
    """收银台 - ¥9.9 支付确认页"""
    order = session.get("pending_valuation")
    if not order:
        return redirect("/valuate")
    if order.get("paid") and session.get("valuation_paid_no") == order["order_no"]:
        return redirect("/valuate/result")

    # 真实支付：若已配置微信官方 API，则在服务端创建支付单并拿到二维码
    store = get_order_store()
    if not order.get("wx_code_url"):
        if payments.WX_CONFIGURED:
            desc = f"房地产全息价值评估报告-{order['community']}"
            order["wx_code_url"] = payments.create_wechat_native(
                order["order_no"], int(round(VALUATION_PRICE * 100)), desc,
                url_for("valuate_pay_notify_wechat", _external=True),
            )
            store.update(order["order_no"], wx_code_url=order.get("wx_code_url"))

    return render_template(
        "valuate_pay.html", order=order, price=VALUATION_PRICE,
        wx_configured=payments.WX_CONFIGURED,
        official=payments.WX_CONFIGURED,
    )


@app.route("/valuate/pay/confirm", methods=["POST"])
def valuate_pay_confirm():
    """确认支付按钮：用户点「支付成功」后直接解锁并生成完整估值报告。

    * 官方支付模式（已配置微信 API）：解锁以回调验签为准，未到账则回收银台等待。
    * 个人收款码模式（未配置官方 API）：用户自主确认已付款，立即解锁报告，无需人工核验。
    """
    order = session.get("pending_valuation")
    if not order:
        return redirect("/valuate")

    pay_method = request.form.get("pay_method", "wechat")
    if pay_method not in ("wechat", "alipay"):
        pay_method = "wechat"

    store = get_order_store()
    official = payments.WX_CONFIGURED or payments.ALI_CONFIGURED

    from datetime import datetime

    # 官方支付模式：解锁取决于回调验签
    if official:
        real_paid = store.get(order["order_no"]) or {}
        if not real_paid.get("paid"):
            return redirect("/valuate/pay?wait=1")
        order["paid"] = True
        order["pay_method"] = real_paid.get("pay_method") or pay_method
        order["paid_at"] = real_paid.get("paid_at")
        session["pending_valuation"] = order
        session["valuation_paid_no"] = order["order_no"]
        logger.info(f"估值订单支付成功(官方) - 单号: {order['order_no']}, 渠道: {pay_method}")
        return redirect("/valuate/result")

    # 个人收款码模式：用户确认已付款 → 直接解锁报告（无需人工核验）
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    order["paid"] = True
    order["pay_method"] = pay_method
    order["paid_at"] = paid_at
    order["review_status"] = "confirmed"
    session["pending_valuation"] = order
    session["valuation_paid_no"] = order["order_no"]
    store.update(order["order_no"], paid=True, pay_method=pay_method, paid_at=paid_at,
                 review_status="confirmed")
    logger.info(f"估值订单已支付并解锁 - 单号: {order['order_no']}, 渠道: {pay_method}")
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
    """估值报告：综合评分/结论/雷达图/Top3 免费，182 维度完整明细需解锁（¥9.9）"""
    order_no = (session.get("pending_valuation") or {}).get("order_no") or session.get("valuation_paid_no")
    order = get_order_store().get(order_no) if order_no else None
    if not order:
        return redirect("/valuate")

    session["pending_valuation"] = order
    session["valuation_paid_no"] = order["order_no"]
    valuation = evaluate_user_input(order["form_data"])
    return render_template(
        "valuate_report.html",
        valuation=valuation,
        order=order,
        is_paid=bool(order.get("paid")),
        price=VALUATION_PRICE,
    )


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