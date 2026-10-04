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
    "https://images.unsplash.com/photo-1589578036109-592d4dadb148?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NDl8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1617104678098-de229db51175?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTB8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1512917774080-9991f1c4c750?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTF8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1645109176591-bc977c1aa35c?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTJ8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1723075471552-26781157140e?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTN8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1676680071181-0a0b45968d23?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTR8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1673119299513-f98a84e4b600?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTV8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1762089424593-c66eb5c51aaa?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTZ8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1682888818696-906287d759f5?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTd8&ixlib=rb-4.1.0&q=80&w=1080",
    "https://images.unsplash.com/photo-1704457031528-adfa0abf6bba?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w5MjM2NzJ8MHwxfHJhbmRvbXx8fHx8fHx8fDE3OTExMDU0NTh8&ixlib=rb-4.1.0&q=80&w=1080",
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
    ap.add_argument("--mode", choices=["merge", "ten"], default="merge",
                    help="merge=按分布批量生成；ten=替换为 10 套代表性示例房源")
    args = ap.parse_args()

    with open(args.out, encoding="utf-8") as f:
        existing = json.load(f)

    if args.mode == "ten":
        # 保留用户真实提交回流（U- 前缀），其余替换为 10 套精选示例
        user_real = [x for x in existing if x.get("id", "").startswith("U-")]
        merged = TEN_DEMO + user_real
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"替换为 {len(TEN_DEMO)} 套代表性示例房源（保留 {len(user_real)} 套用户真实房源），总计 {len(merged)} 套")
        return

    existing_ids = {x["id"] for x in existing}
    fresh = [x for x in generate(args.count) if x["id"] not in existing_ids]
    merged = fresh + existing  # 新生成的排前面
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"新增 {len(fresh)} 套，总计 {len(merged)} 套（原有 {len(existing)} 套保留）")


# ---------------------------------------------------------------------------
# 10 套代表性示例房源（覆盖北京二手房的典型户型/价位/板块档位）
# 全部为虚构数据，title/community/description 显性标注"示例"
# ---------------------------------------------------------------------------
_T = "[示例] "
_DEMO_NOTE = "虚假示例房源：本条为产品演示用的虚构数据，不对应任何真实在售房屋，请勿据此交易。"

TEN_DEMO = [
    {"id": "DEMO-01", "title": _T + "刚需电梯两居，近地铁满五唯一", "community": "回龙观·示范家园",
     "address": "昌平区回龙观", "layout": "2室2厅1卫", "area": 89.0, "price": 415.0,
     "tags": ["示例房源", "近地铁", "满五唯一", "精装"], "description": _DEMO_NOTE + "89㎡两居，精装，2008年建，代表昌平地铁刚需盘。"},
    {"id": "DEMO-02", "title": _T + "海淀学区三居，重点小学划片", "community": "中关村·示范公寓",
     "address": "海淀区中关村", "layout": "3室1厅1卫", "area": 91.0, "price": 980.0,
     "tags": ["示例房源", "学区房", "南北通透"], "description": _DEMO_NOTE + "91㎡三居，代表海淀学区房价位。"},
    {"id": "DEMO-03", "title": _T + "核心区老公房两居，对口位移方便", "community": "广外·示范里",
     "address": "西城区广安门", "layout": "2室1厅1卫", "area": 58.0, "price": 505.0,
     "tags": ["示例房源", "1995年建", "无电梯"], "description": _DEMO_NOTE + "58㎡老公房两居，代表西城老破小价位。"},
    {"id": "DEMO-04", "title": _T + "望京改善三居，双卫全明格局", "community": "望京·示范园",
     "address": "朝阳区望京", "layout": "3室2厅2卫", "area": 121.0, "price": 820.0,
     "tags": ["示例房源", "南北通透", "电梯房"], "description": _DEMO_NOTE + "121㎡三居，代表朝阳改善盘。"},
    {"id": "DEMO-05", "title": _T + "远郊低总价两居，首套上车盘", "community": "良乡·示范家园",
     "address": "房山区良乡", "layout": "2室2厅1卫", "area": 88.0, "price": 185.0,
     "tags": ["示例房源", "低总价", "随时看房"], "description": _DEMO_NOTE + "88㎡两居，代表远郊上车盘价位。"},
    {"id": "DEMO-06", "title": _T + "朝阳公园大平层，四居双阳台", "community": "朝阳公园·示范府",
     "address": "朝阳区朝阳公园", "layout": "4室2厅3卫", "area": 205.0, "price": 1680.0,
     "tags": ["示例房源", "大平层", "豪华装修"], "description": _DEMO_NOTE + "205㎡大平层，代表高端改善价位。"},
    {"id": "DEMO-07", "title": _T + "核心区小开间，低总价上车", "community": "广安门·示范小寓",
     "address": "西城区广安门", "layout": "1室1厅1卫", "area": 29.6, "price": 208.0,
     "tags": ["示例房源", "低总价", "临地铁"], "description": _DEMO_NOTE + "29.6㎡开间，代表小户型总价段。"},
    {"id": "DEMO-08", "title": _T + "通州次新电梯两居，满五唯一", "community": "梨园·示范城",
     "address": "通州区梨园", "layout": "2室2厅2卫", "area": 89.0, "price": 305.0,
     "tags": ["示例房源", "满五唯一", "电梯房"], "description": _DEMO_NOTE + "89㎡两居，代表通州次新盘。"},
    {"id": "DEMO-09", "title": _T + "亦庄豪装四居，园区环境", "community": "亦庄·示范郡",
     "address": "经开区亦庄", "layout": "4室2厅2卫", "area": 168.0, "price": 1020.0,
     "tags": ["示例房源", "豪华装修", "人车分流"], "description": _DEMO_NOTE + "168㎡四居，代表亦庄改善价位。"},
    {"id": "DEMO-10", "title": _T + "丽泽南向三居，商务区配套", "community": "丽泽·示范苑",
     "address": "丰台区丽泽", "layout": "3室2厅2卫", "area": 106.0, "price": 565.0,
     "tags": ["示例房源", "满五唯一", "南北通透"], "description": _DEMO_NOTE + "106㎡三居，代表丰台丽泽板块。"},
]
for _i, _d in enumerate(TEN_DEMO):
    _d["image_url"] = IMAGE_POOL[_i % len(IMAGE_POOL)]
    _d["latitude"] = None
    _d["longitude"] = None


if __name__ == "__main__":
    main()
