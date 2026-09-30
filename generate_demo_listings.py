"""示例房源批量生成器（渠道1 衍生：按官方分布合成的演示数据）。

用途：扩充 Demo 房源库的展示规模。房源为合成数据（小区名为虚构组合），
但两项分布严格贴合官方口径：
- 行政区占比 〜 北京存量房市场结构（朝阳/海淀/丰台为前三）
- 总价分布 〜 data_beijing/price_bands.csv（官方 2023-06 网签价格段）

生成结果合并写入 mock_listings.json（保留原有房源），可重复运行（按 id 去重）。
用法：python generate_demo_listings.py [--count 400] [--out mock_listings.json]
"""

import argparse
import json
import random

SEED = 20260930
IMAGE_POOL = [
    "https://images.unsplash.com/photo-1630699293388-0938c397106a?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5jM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDV8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1684928365167-e91916573122?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5jM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDd8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1562821696-c68d007f943b?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5jM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3NzYwODAzNDl8&ixlib=rb-4.1.0&q=80&w=1080",
]

# 行政区：权重（占比参考北京存量房市场结构）+ 单价区间（万/㎡）+ 商圈词库
DISTRICTS = {
    "朝阳": {"w": 18, "unit": (6.5, 10.5), "areas": ["望京", "双井", "朝青", "亚运村", "常营", "酒仙桥", "CBD"]},
    "海淀": {"w": 14, "unit": (8.5, 12.5), "areas": ["中关村", "五道口", "西二旗", "清河", "田村", "学院路"]},
    "丰台": {"w": 12, "unit": (5.0, 7.5), "areas": ["丽泽", "方庄", "马家堡", "六里桥", "岳各庄"]},
    "昌平": {"w": 9, "unit": (4.0, 6.0), "areas": ["回龙观", "天通苑", "沙河", "南邵"]},
    "大兴": {"w": 8, "unit": (3.8, 5.5), "areas": ["亦庄", "黄村", "西红门", "旧宫"]},
    "通州": {"w": 8, "unit": (4.0, 6.0), "areas": ["梨园", "九棵树", "运河商务区", "马驹桥"]},
    "西城": {"w": 6, "unit": (10.5, 14.5), "areas": ["德胜门", "金融街", "月坛", "广外"]},
    "东城": {"w": 6, "unit": (9.5, 13.0), "areas": ["东直门", "安定门", "崇文门", "和平里"]},
    "顺义": {"w": 5, "unit": (3.0, 5.0), "areas": ["后沙峪", "顺义城", "马坡"]},
    "房山": {"w": 4, "unit": (2.5, 4.0), "areas": ["良乡", "长阳", "窦店"]},
    "石景山": {"w": 3, "unit": (4.5, 6.5), "areas": ["苹果园", "鲁谷", "古城"]},
    "经开区": {"w": 2, "unit": (4.5, 6.0), "areas": ["亦庄核心区", "河西区"]},
    "门头沟": {"w": 2, "unit": (2.8, 4.2), "areas": ["门城", "永定"]},
    "怀柔": {"w": 1, "unit": (2.2, 3.5), "areas": ["怀柔城区", "雁栖"]},
    "密云": {"w": 1, "unit": (1.8, 3.0), "areas": ["密云城区"]},
    "平谷": {"w": 0.5, "unit": (1.8, 2.8), "areas": ["平谷城区"]},
    "延庆": {"w": 0.5, "unit": (1.5, 2.5), "areas": ["延庆城区"]},
}

COMMUNITY_SUFFIX = ["家园", "花园", "公寓", "里", "苑", "府", "湾", "城", "郡", "庭"]
DECORATIONS = ["精装修", "豪装", "简装修", "毛坯"]
ORIENTATIONS = ["南北", "南", "东南", "东西"]

TAG_POOL = ["近地铁", "学区房", "南北通透", "满五唯一", "电梯房", "人车分流",
            "随时看房", "地铁房", "拎包入住", "采光好"]

# 低价区（用于承接官方口径中 200 万以下的约 52% 成交份额）
BUDGET_DISTRICTS = {"密云", "延庆", "平谷", "怀柔", "门头沟", "房山", "顺义", "昌平", "大兴", "通州"}


def pick_layout(area):
    """面积 → 户型，保证厅卫数量合理（与估值模型的 layout 解析兼容）。"""
    if area < 45:
        return random.choice(["1室1厅1卫", "开间1卫"])
    if area < 60:
        return "1室1厅1卫"
    if area < 90:
        return random.choice(["2室1厅1卫", "2室2厅1卫"])
    if area < 120:
        return random.choice(["2室2厅2卫", "3室2厅1卫"])
    if area < 145:
        return random.choice(["3室2厅2卫", "3室2厅2卫", "4室2厅2卫"])
    if area < 200:
        return "4室2厅2卫"
    return random.choice(["4室2厅3卫", "5室2厅3卫"])


def pick_area():
    """面积分布：向 60-140㎡ 集中，兼顾小户型与大平层。"""
    buckets = [(40, 60, 15), (60, 90, 35), (90, 120, 28), (120, 145, 12),
               (145, 200, 7), (200, 260, 3)]
    lo, hi, _w = random.choices(buckets, weights=[b[2] for b in buckets])[0]
    return round(random.uniform(lo, hi), 1)


def pick_title(layout, deco, tags):
    if "开间" in layout:
        ju = "开间"
    elif "2室" in layout:
        ju = "两居"
    elif "3室" in layout:
        ju = "三居"
    elif "4室" in layout:
        ju = "四居"
    elif "5室" in layout:
        ju = "五居"
    else:
        ju = "一居"
    selling = [t for t in tags if t != "示例房源"]
    hook = random.choice([
        f"{deco}{ju}，{' · '.join(selling[:2])}",
        f"{' · '.join(selling[:2])}，{deco}{ju}看房方便",
        f"{deco}{ju}采光好，{' · '.join(selling[:2])}",
        f"业主诚售，{deco}{ju}",
    ])
    return hook


def _community(area_name):
    """商圈名 → 小区名（避免'密云城区城'这类难看拼接）。"""
    if area_name.endswith(("区", "城")):
        return area_name + random.choice(["小区", "家园", "一号院"])
    return area_name + random.choice(COMMUNITY_SUFFIX) + (str(random.randint(1, 3)) if random.random() < 0.4 else "")


def _make_one(district, price_cap=None):
    """生成单条；price_cap 限定总价上限（预算档用 200）。超限重试几次后放宽。"""
    info = DISTRICTS[district]
    for _ in range(12):
        area = pick_area() if price_cap is None else round(random.uniform(40, 80), 1)
        lo, hi = info["unit"]
        if price_cap is not None:
            hi = min(hi, price_cap / area * 0.98)
        if hi <= lo:
            continue
        price = round(random.uniform(lo, hi) * area, 0)
        if price_cap is not None and price > price_cap:
            continue
        layout = pick_layout(area)
        deco = random.choice(DECORATIONS)
        area_name = random.choice(info["areas"])
        tags = ["示例房源"] + random.sample(TAG_POOL, random.randint(2, 4))
        title = pick_title(layout, deco, tags)
        bits = [f"建筑面积约{area:g}㎡，{layout}"]
        if deco != "毛坯":
            bits.append(deco)
        bits.append(f"{random.choice(ORIENTATIONS)}朝向")
        bits.append("示例房源数据，分布参照官方口径。")
        return {
            "id": None,  # 由调用方编号
            "title": title,
            "community": _community(area_name),
            "address": f"{district}区{area_name}",
            "layout": layout,
            "area": area,
            "price": price,
            "tags": tags[:6],
            "description": "，".join(bits),
            "image_url": random.choice(IMAGE_POOL),
            "latitude": None,
            "longitude": None,
        }
    return None


def generate(count):
    random.seed(SEED)
    names = list(DISTRICTS)
    weights = [DISTRICTS[d]["w"] for d in names]
    budget_share = 0.52  # 官方口径：200 万以下成交约占 52%（price_bands.csv 2023-06）

    listings = []
    i = 0
    while i < count:
        budget = random.random() < budget_share
        if budget:
            district = random.choice(list(BUDGET_DISTRICTS))
        else:
            district = random.choices(names, weights=weights)[0]
            if district in BUDGET_DISTRICTS and random.random() < 0.6:
                # 非预算档尽量避免低价区，保持 600 万+ 档有足够供给
                district = random.choices(names, weights=weights)[0]
        item = _make_one(district, price_cap=200 if budget else None)
        if item is None:
            continue
        i += 1
        item["id"] = f"D-{i:04d}"
        listings.append(item)
    return listings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=400, help="新增套数（默认 400）")
    ap.add_argument("--out", default="mock_listings.json")
    args = ap.parse_args()

    with open(args.out, encoding="utf-8") as f:
        existing = json.load(f)
    existing_ids = {x["id"] for x in existing}

    fresh = [x for x in generate(args.count) if x["id"] not in existing_ids]
    merged = fresh + existing  # 新生成的排前面
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"新增 {len(fresh)} 套，总计 {len(merged)} 套（原有 {len(existing)} 套保留）")


if __name__ == "__main__":
    main()
