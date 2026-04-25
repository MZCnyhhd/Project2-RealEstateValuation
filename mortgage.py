from typing import Dict


def _validate_positive(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} 不能为负数")


def _monthly_rate(annual_rate_pct: float) -> float:
    return (annual_rate_pct / 100.0) / 12.0


def calculate_mortgage(
    total_price_wan: float,
    down_payment_ratio_pct: float,
    years: int,
    annual_rate_pct: float,
    repayment_method: str = "equal_payment",
    monthly_income: float = 0.0,
    monthly_debt: float = 0.0,
) -> Dict[str, float]:
    _validate_positive("总价", total_price_wan)
    _validate_positive("首付比例", down_payment_ratio_pct)
    _validate_positive("贷款年限", years)
    _validate_positive("年化利率", annual_rate_pct)
    _validate_positive("月收入", monthly_income)
    _validate_positive("现有月负债", monthly_debt)

    if down_payment_ratio_pct > 100:
        raise ValueError("首付比例不能超过 100%")
    if years <= 0:
        raise ValueError("贷款年限必须大于 0")

    total_price = total_price_wan * 10000.0
    down_payment = total_price * (down_payment_ratio_pct / 100.0)
    loan_principal = max(total_price - down_payment, 0.0)
    months = int(years * 12)
    r = _monthly_rate(annual_rate_pct)

    if loan_principal == 0:
        monthly_payment = 0.0
        first_month_payment = 0.0
        total_interest = 0.0
        total_repayment = total_price
    elif repayment_method == "equal_principal":
        principal_per_month = loan_principal / months
        first_month_payment = principal_per_month + loan_principal * r
        total_interest = ((months + 1) * loan_principal * r) / 2.0
        total_repayment = loan_principal + total_interest + down_payment
        monthly_payment = first_month_payment
    else:
        # 等额本息
        if r == 0:
            monthly_payment = loan_principal / months
        else:
            monthly_payment = (
                loan_principal * r * (1 + r) ** months / ((1 + r) ** months - 1)
            )
        first_month_payment = monthly_payment
        total_repayment_loan_part = monthly_payment * months
        total_interest = total_repayment_loan_part - loan_principal
        total_repayment = total_repayment_loan_part + down_payment

    dsr = 0.0
    if monthly_income > 0:
        dsr = (monthly_payment + monthly_debt) / monthly_income

    if dsr <= 0.3:
        pressure_level = "安全"
    elif dsr <= 0.5:
        pressure_level = "谨慎"
    else:
        pressure_level = "高压"

    return {
        "total_price_wan": round(total_price_wan, 2),
        "down_payment_ratio_pct": round(down_payment_ratio_pct, 2),
        "years": years,
        "annual_rate_pct": round(annual_rate_pct, 3),
        "repayment_method": repayment_method,
        "down_payment_yuan": round(down_payment, 2),
        "loan_principal_yuan": round(loan_principal, 2),
        "monthly_payment_yuan": round(monthly_payment, 2),
        "first_month_payment_yuan": round(first_month_payment, 2),
        "total_interest_yuan": round(total_interest, 2),
        "total_repayment_yuan": round(total_repayment, 2),
        "monthly_income": round(monthly_income, 2),
        "monthly_debt": round(monthly_debt, 2),
        "dsr": round(dsr, 4),
        "pressure_level": pressure_level,
    }
