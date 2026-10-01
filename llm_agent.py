import json
import os
import logging
from typing import Dict, Tuple

# 配置日志
logger = logging.getLogger(__name__)

from beijing_policy import apply_beijing_policy
from chat_agent import build_agent_reply as build_rule_reply
from mortgage import calculate_mortgage


SYSTEM_PROMPT = """你是北京房贷测算助手。你的任务是从用户中文输入中提取结构化参数。
你只能输出 JSON，不要输出其他文本。
字段名必须严格使用以下英文键，禁止使用中文键名：
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
输出示例（严格遵循此结构和键名）：
用户：预算800万，首付35%，贷30年，等额本息
输出：{"params": {"total_price_wan": 800, "down_payment_ratio_pct": 35, "years": 30, "repayment_method": "equal_payment"}, "missing_fields": ["annual_rate_pct", "monthly_income", "monthly_debt", "purchase_type", "housing_type", "loan_type"]}
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


def _resolve_llm_config(purpose: str = "chat"):
    """解析 LLM 配置。全项目统一接入小米 MiMo 开放平台（OpenAI 兼容协议）。

    purpose 仅为兼容历史调用（chat=客服对话 / extract=房贷参数提取），配置同源。
    返回 dict(api_key/base_url/model/provider) 或 None（未配置 MIMO_API_KEY）。
    """
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip(),
        "model": os.environ.get("MIMO_MODEL", "mimo-v2.5").strip(),
        "provider": "MiMo",
    }


def _load_openai_client(purpose: str = "chat"):
    config = _resolve_llm_config(purpose)
    if config is None:
        logger.warning("LLM API Key 未设置（MIMO_API_KEY）")
        return None
    try:
        from openai import OpenAI
    except Exception as e:
        logger.error(f"导入OpenAI库失败: {str(e)}")
        return None

    logger.info("LLM 客户端初始化成功: %s (%s)", config["provider"], config["model"])
    return OpenAI(api_key=config["api_key"], base_url=config["base_url"])


def _llm_extract_params(message: str) -> Tuple[Dict, list]:
    client = _load_openai_client(purpose="extract")
    if client is None:
        raise RuntimeError("LLM 不可用：缺少 MIMO_API_KEY 或 openai 依赖")

    config = _resolve_llm_config(purpose="extract")
    model = config["model"]
    logger.info(f"调用模型: {model}")
    
    try:
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
        logger.info("阿里云百炼调用成功")
    except Exception as e:
        logger.error(f"阿里云百炼调用失败: {str(e)}")
        raise
    
    data = json.loads(content)
    params = data.get("params", {})
    missing_fields = data.get("missing_fields", [])
    if not isinstance(params, dict):
        params = {}
    if not isinstance(missing_fields, list):
        missing_fields = []
    logger.info(f"提取参数: {params}, 缺失字段: {missing_fields}")
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
        logger.info("尝试使用Qwen-Turbo大模型处理请求")
        llm_params, missing_fields = _llm_extract_params(message)
        normalized = _normalize_params(llm_params)
        merged = dict(DEFAULTS)
        merged.update(normalized)
        logger.info("Qwen-Turbo大模型处理成功")
        return _build_final_reply(merged, missing_fields)
    except Exception as e:
        logger.warning(f"阿里云百炼大模型处理失败，回退到规则解析: {str(e)}")
        # 任何 LLM 异常都回退规则解析，保证服务可用
        return build_rule_reply(message)


# ============ 通用客服 LLM（房·车智能客服使用）============
CS_SYSTEM_PROMPT = """你是「房·车智能推荐客服助手」，服务一个同时经营北京房产信息与新能源车（小米汽车、理想汽车）的平台。
职责边界（只回答以下业务范围）：
1. 北京房产：房价、房贷、购房政策/税费/限购、房源推荐、看房预约
2. 新能源车：小米 SU7/YU7 系列与理想 L 系/MEGA 的价格、续航、增程/纯电补能、购车权益、试驾预约（只介绍这两个品牌，其他品牌礼貌说明不代理）

【内部工具结果使用规则】
- 若用户消息附带【内部工具结果】：那是系统推荐工具/知识库检索出的真实数据（房源卡片、车型卡片、FAQ 答案），你必须基于这些数据组织回复，用自然亲切的口吻重新表达，保留关键参数（价格/户型/续航/链接等），不要新增数据里不存在的房源、车型或价格。
- 若没有附带工具结果：基于你的业务知识回答，但不要编造具体房源或车型参数，可给出建议并引导用户补充预算/需求。

【通用要求】
- 无关话题（闲聊、民俗、新闻、天气、其他行业）：不要展开回答，礼貌说明你只负责房产与汽车业务，并用一句话引导用户回到业务，或建议回复「转人工」
- 简洁友好，150 字以内；涉及具体价格政策时提醒以门店/最新政策为准
- 不确定时建议用户回复「转人工」；不要编造房源和车型参数
"""


def general_chat(message: str, tool_context: str = None) -> str:
    """通用客服对话主引擎（不限房贷场景）。LLM 不可用时抛异常，由调用方降级。

    tool_context: 内部工具/知识库检索出的真实数据（房源卡片、车型卡片、FAQ 答案），
    非空时大模型必须基于它组织回复，保证参数真实。
    """
    client = _load_openai_client(purpose="chat")
    if client is None:
        raise RuntimeError("LLM 不可用：缺少 MIMO_API_KEY 或 openai 依赖")
    config = _resolve_llm_config(purpose="chat")
    user_content = message
    if tool_context:
        user_content = f"{message}\n\n【内部工具结果（真实数据，回复必须基于它）】\n{tool_context}"
    resp = client.chat.completions.create(
        model=config["model"],
        temperature=0.5,
        messages=[
            {"role": "system", "content": CS_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
