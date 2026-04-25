import re
from typing import Dict, Tuple

from mortgage import calculate_mortgage
from beijing_policy import apply_beijing_policy


def _extract_float(text: str, pattern: str):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1))


def parse_user_input(message: str) -> Dict[str, float]:
    text = (message or "").strip()
    params: Dict[str, float] = {}

    total_price = _extract_float(text, r"预算\s*([0-9]+(?:\.[0-9]+)?)\s*万")
    if total_price is None:
        total_price = _extract_float(text, r"总价\s*([0-9]+(?:\.[0-9]+)?)\s*万")
    if total_price is not None:
        params["total_price_wan"] = total_price

    down_ratio = _extract_float(text, r"首付\s*([0-9]+(?:\.[0-9]+)?)\s*%")
    if down_ratio is not None:
        params["down_payment_ratio_pct"] = down_ratio

    years = _extract_float(text, r"([0-9]+(?:\.[0-9]+)?)\s*年")
    if years is not None:
        params["years"] = int(years)

    rate = _extract_float(text, r"(?:利率|年化)\s*([0-9]+(?:\.[0-9]+)?)\s*%")
    if rate is not None:
        params["annual_rate_pct"] = rate

    income = _extract_float(text, r"月收入\s*([0-9]+(?:\.[0-9]+)?)\s*(?:万|元)?")
    if income is not None:
        if "万" in text[text.find("月收入"): text.find("月收入") + 15]:
            params["monthly_income"] = income * 10000
        else:
            params["monthly_income"] = income

    debt = _extract_float(text, r"(?:负债|月负债)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:万|元)?")
    if debt is not None:
        if "万" in text[text.find("负债"): text.find("负债") + 10]:
            params["monthly_debt"] = debt * 10000
        else:
            params["monthly_debt"] = debt

    if "等额本金" in text:
        params["repayment_method"] = "equal_principal"
    elif "等额本息" in text:
        params["repayment_method"] = "equal_payment"

    if "首套" in text:
        params["purchase_type"] = "first_home"
    elif "二套" in text:
        params["purchase_type"] = "second_home"

    if "非普" in text:
        params["housing_type"] = "non_normal"
    elif "普宅" in text:
        params["housing_type"] = "normal"

    if "公积金" in text and "组合" in text:
        params["loan_type"] = "combined"
    elif "公积金" in text:
        params["loan_type"] = "fund"
    elif "商贷" in text or "商业贷款" in text:
        params["loan_type"] = "commercial"

    return params


def build_agent_reply(message: str) -> Tuple[str, Dict]:
    extracted = parse_user_input(message)
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
    defaults.update(extracted)

    missing = []
    for key in ("total_price_wan", "down_payment_ratio_pct", "years", "annual_rate_pct"):
        if key not in extracted:
            missing.append(key)

    policy_meta = apply_beijing_policy(
        down_payment_ratio_pct=defaults["down_payment_ratio_pct"],
        annual_rate_pct=defaults["annual_rate_pct"],
        purchase_type=defaults["purchase_type"],
        housing_type=defaults["housing_type"],
        loan_type=defaults["loan_type"],
    )

    calc_payload = {
        "total_price_wan": defaults["total_price_wan"],
        "down_payment_ratio_pct": policy_meta["effective_down_payment_ratio_pct"],
        "years": defaults["years"],
        "annual_rate_pct": policy_meta["effective_annual_rate_pct"],
        "repayment_method": defaults["repayment_method"],
        "monthly_income": defaults["monthly_income"],
        "monthly_debt": defaults["monthly_debt"],
    }
    result = calculate_mortgage(**calc_payload)
    method_text = "等额本息" if result["repayment_method"] == "equal_payment" else "等额本金"

    advice = [
        f"按你给的信息（{method_text}），估算月供约 {result['monthly_payment_yuan']:.0f} 元，"
        f"首付约 {result['down_payment_yuan']:.0f} 元，总利息约 {result['total_interest_yuan']:.0f} 元。",
        f"北京政策生效后：首付比例按 {policy_meta['effective_down_payment_ratio_pct']:.1f}% 计算，"
        f"年化利率按 {policy_meta['effective_annual_rate_pct']:.2f}% 计算（政策下限首付 {policy_meta['policy_min_down_pct']:.1f}%）。",
        f"月供收入比（含现有负债）约 {result['dsr'] * 100:.1f}%，压力等级：{result['pressure_level']}。",
    ]
    if missing:
        mapping = {
            "total_price_wan": "总价/预算（万）",
            "down_payment_ratio_pct": "首付比例（%）",
            "years": "贷款年限（年）",
            "annual_rate_pct": "年化利率（%）",
        }
        advice.append(
            "我先用默认值补齐了部分参数，若要更准请补充："
            + "、".join(mapping[x] for x in missing)
            + "。"
        )

    advice.append("提示：结果仅供参考，不构成购房建议。")
    result["policy"] = policy_meta
    result["purchase_type"] = defaults["purchase_type"]
    result["housing_type"] = defaults["housing_type"]
    result["loan_type"] = defaults["loan_type"]
    return "\n".join(advice), result
