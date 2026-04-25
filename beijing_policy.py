from typing import Dict


# 可参数化政策表（示例值，便于后续按最新政策调整）
POLICY_TABLE = {
    "first_home": {
        "normal": {
            "commercial": {"min_down_pct": 35.0, "default_rate_pct": 3.1},
            "fund": {"min_down_pct": 20.0, "default_rate_pct": 2.85},
            "combined": {"min_down_pct": 30.0, "default_rate_pct": 3.0},
        },
        "non_normal": {
            "commercial": {"min_down_pct": 40.0, "default_rate_pct": 3.2},
            "fund": {"min_down_pct": 25.0, "default_rate_pct": 2.85},
            "combined": {"min_down_pct": 35.0, "default_rate_pct": 3.05},
        },
    },
    "second_home": {
        "normal": {
            "commercial": {"min_down_pct": 60.0, "default_rate_pct": 3.6},
            "fund": {"min_down_pct": 40.0, "default_rate_pct": 3.1},
            "combined": {"min_down_pct": 55.0, "default_rate_pct": 3.45},
        },
        "non_normal": {
            "commercial": {"min_down_pct": 80.0, "default_rate_pct": 3.9},
            "fund": {"min_down_pct": 50.0, "default_rate_pct": 3.25},
            "combined": {"min_down_pct": 70.0, "default_rate_pct": 3.7},
        },
    },
}


def _validate_option(name: str, value: str, candidates) -> None:
    if value not in candidates:
        raise ValueError(f"{name} 不合法: {value}")


def get_policy_config(
    purchase_type: str = "first_home",
    housing_type: str = "normal",
    loan_type: str = "commercial",
) -> Dict[str, float]:
    _validate_option("购房属性", purchase_type, ("first_home", "second_home"))
    _validate_option("住宅属性", housing_type, ("normal", "non_normal"))
    _validate_option("贷款类型", loan_type, ("commercial", "fund", "combined"))
    return POLICY_TABLE[purchase_type][housing_type][loan_type]


def apply_beijing_policy(
    down_payment_ratio_pct: float,
    annual_rate_pct: float,
    purchase_type: str,
    housing_type: str,
    loan_type: str,
) -> Dict[str, float]:
    policy = get_policy_config(
        purchase_type=purchase_type,
        housing_type=housing_type,
        loan_type=loan_type,
    )
    effective_down = max(down_payment_ratio_pct, policy["min_down_pct"])
    effective_rate = annual_rate_pct if annual_rate_pct > 0 else policy["default_rate_pct"]
    return {
        "policy_min_down_pct": policy["min_down_pct"],
        "policy_default_rate_pct": policy["default_rate_pct"],
        "effective_down_payment_ratio_pct": effective_down,
        "effective_annual_rate_pct": effective_rate,
    }
