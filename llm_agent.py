import json
import os
from typing import Dict, Tuple

from beijing_policy import apply_beijing_policy
from chat_agent import build_agent_reply as build_rule_reply
from mortgage import calculate_mortgage


SYSTEM_PROMPT = """你是北京房贷测算助手。你的任务是从用户中文输入中提取结构化参数。
你只能输出 JSON，不要输出其他文本。
字段要求：
- total_price_wan: 总价，单位万
- down_payment_ratio_pct: 首付比例，百分比数值
- years: 贷款年限（整数）
- annual_rate_pct: 年化利率（百分比）
- repayment_method: equal_payment 或 equal_principal
- monthly_income: 月收入（元）
- monthly_debt: 月负债（元）
- purchase_type: first_home 或 second_home
- housing_type: normal 或 non_normal
- loan_type: commercial 或 fund 或 combined

如果用户没有提及某字段，不要臆造，放在 missing_fields 里。
JSON 格式：
{
  "params": {...},
  "missing_fields": []
}
"""


DEFAULTS = {
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


def _load_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None

    kwargs = {"api_key": api_key}
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _llm_extract_params(message: str) -> Tuple[Dict, list]:
    client = _load_openai_client()
    if client is None:
        raise RuntimeError("LLM 不可用：缺少 OPENAI_API_KEY 或 openai 依赖")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip()
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    params = data.get("params", {})
    missing_fields = data.get("missing_fields", [])
    if not isinstance(params, dict):
        params = {}
    if not isinstance(missing_fields, list):
        missing_fields = []
    return params, missing_fields


def _normalize_params(raw_params: Dict) -> Dict:
    params = {}
    for key, default_val in DEFAULTS.items():
        if key not in raw_params:
            continue
        value = raw_params[key]
        if isinstance(default_val, float):
            try:
                params[key] = float(value)
            except Exception:
                continue
        elif isinstance(default_val, int):
            try:
                params[key] = int(float(value))
            except Exception:
                continue
        else:
            params[key] = str(value).strip()

    if params.get("repayment_method") not in ("equal_payment", "equal_principal"):
        params.pop("repayment_method", None)
    if params.get("purchase_type") not in ("first_home", "second_home"):
        params.pop("purchase_type", None)
    if params.get("housing_type") not in ("normal", "non_normal"):
        params.pop("housing_type", None)
    if params.get("loan_type") not in ("commercial", "fund", "combined"):
        params.pop("loan_type", None)
    return params


def _build_final_reply(merged: Dict, missing_fields: list) -> Tuple[str, Dict]:
    policy_meta = apply_beijing_policy(
        down_payment_ratio_pct=merged["down_payment_ratio_pct"],
        annual_rate_pct=merged["annual_rate_pct"],
        purchase_type=merged["purchase_type"],
        housing_type=merged["housing_type"],
        loan_type=merged["loan_type"],
    )
    calc = calculate_mortgage(
        total_price_wan=merged["total_price_wan"],
        down_payment_ratio_pct=policy_meta["effective_down_payment_ratio_pct"],
        years=merged["years"],
        annual_rate_pct=policy_meta["effective_annual_rate_pct"],
        repayment_method=merged["repayment_method"],
        monthly_income=merged["monthly_income"],
        monthly_debt=merged["monthly_debt"],
    )
    method_text = "等额本息" if calc["repayment_method"] == "equal_payment" else "等额本金"

    reply_lines = [
        f"按你给的信息（{method_text}），估算月供约 {calc['monthly_payment_yuan']:.0f} 元，"
        f"首付约 {calc['down_payment_yuan']:.0f} 元，总利息约 {calc['total_interest_yuan']:.0f} 元。",
        f"北京政策生效后：首付比例按 {policy_meta['effective_down_payment_ratio_pct']:.1f}% 计算，"
        f"年化利率按 {policy_meta['effective_annual_rate_pct']:.2f}% 计算（政策下限首付 {policy_meta['policy_min_down_pct']:.1f}%）。",
        f"月供收入比（含现有负债）约 {calc['dsr'] * 100:.1f}%，压力等级：{calc['pressure_level']}。",
    ]
    if missing_fields:
        mapping = {
            "total_price_wan": "总价/预算（万）",
            "down_payment_ratio_pct": "首付比例（%）",
            "years": "贷款年限（年）",
            "annual_rate_pct": "年化利率（%）",
            "repayment_method": "还款方式",
            "monthly_income": "月收入（元）",
            "monthly_debt": "月负债（元）",
            "purchase_type": "首套/二套",
            "housing_type": "普宅/非普宅",
            "loan_type": "贷款类型",
        }
        fields = [mapping.get(x, x) for x in missing_fields]
        reply_lines.append("缺少参数已按默认值估算，可补充： " + "、".join(fields) + "。")
    reply_lines.append("提示：结果仅供参考，不构成购房建议。")

    calc["policy"] = policy_meta
    calc["purchase_type"] = merged["purchase_type"]
    calc["housing_type"] = merged["housing_type"]
    calc["loan_type"] = merged["loan_type"]
    return "\n".join(reply_lines), calc


def build_agent_reply(message: str) -> Tuple[str, Dict]:
    try:
        llm_params, missing_fields = _llm_extract_params(message)
        normalized = _normalize_params(llm_params)
        merged = dict(DEFAULTS)
        merged.update(normalized)
        return _build_final_reply(merged, missing_fields)
    except Exception:
        # 任何 LLM 异常都回退规则解析，保证服务可用
        return build_rule_reply(message)
