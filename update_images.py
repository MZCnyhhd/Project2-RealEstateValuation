import json
import requests
import random

# 您的 Unsplash Access Key
ACCESS_KEY = 'vZzUJ3tQ8ugb0mTKgjmuWvbKFtpSSxAxyJ6dCsoY4TI'
API_URL = 'https://api.unsplash.com/photos/random'

def get_real_estate_image():
    """从Unsplash获取一张随机的房地产相关图片"""
    params = {
        'client_id': ACCESS_KEY,
        'query': random.choice(['living room', 'interior', 'modern house', 'kitchen', 'bedroom']),
        'orientation': 'landscape',
    }
    try:
        response = requests.get(API_URL, params=params)
        response.raise_for_status()  # 如果请求失败则抛出异常
        data = response.json()
        # 我们使用 'regular' 尺寸的图片，质量和大小都比较合适
        return data['urls']['regular']
    except requests.exceptions.RequestException as e:
        print(f"请求图片失败: {e}")
        return None

def update_listing_images(filename="mock_listings.json"):
    """更新JSON文件中的所有房源图片"""
    try:
        with open(filename, 'r+', encoding='utf-8') as f:
            listings = json.load(f)
            print(f"成功加载 {len(listings)} 条房源数据，开始更新图片...")

            for i, listing in enumerate(listings):
                print(f"正在更新第 {i+1}/{len(listings)} 条房源: {listing['id']}")
                new_image_url = get_real_estate_image()
                if new_image_url:
                    listing['image_url'] = new_image_url
                    print(f"  -> 成功获取新图片URL")
                else:
                    print(f"  -> 获取图片失败，跳过此房源")
            
            # 回到文件开头，清空文件，然后写入更新后的数据
            f.seek(0)
            f.truncate()
            json.dump(listings, f, ensure_ascii=False, indent=2)
            print("\n所有房源图片更新完毕！")

    except FileNotFoundError:
        print(f"错误: 未找到文件 {filename}")
    except json.JSONDecodeError:
        print(f"错误: {filename} 文件格式不正确")


def _parse_listing_number(listing_id):
    if not isinstance(listing_id, str):
        return None
    if not listing_id.startswith("R"):
        return None
    try:
        return int(listing_id[1:])
    except ValueError:
        return None


def _next_listing_number(existing_listings):
    nums = []
    for item in existing_listings:
        n = _parse_listing_number(item.get("id"))
        if n is not None:
            nums.append(n)
    return (max(nums) + 1) if nums else 1


def _random_layout():
    choices = [
        ("1室0厅1卫", 0.10),
        ("1室1厅1卫", 0.18),
        ("2室1厅1卫", 0.22),
        ("2室2厅1卫", 0.10),
        ("3室1厅1卫", 0.06),
        ("3室2厅2卫", 0.22),
        ("4室2厅2卫", 0.08),
        ("4室2厅3卫", 0.03),
        ("5室3厅3卫", 0.01),
    ]
    r = random.random()
    acc = 0.0
    for value, p in choices:
        acc += p
        if r <= acc:
            return value
    return choices[-1][0]


def _layout_rooms(layout):
    if not isinstance(layout, str):
        return 2
    try:
        return int(layout.split("室")[0])
    except Exception:
        return 2


def _random_area(layout):
    rooms = _layout_rooms(layout)
    base = {
        1: 42,
        2: 85,
        3: 118,
        4: 165,
        5: 260,
    }.get(rooms, 95)
    jitter = random.uniform(-0.18, 0.22)
    area = base * (1.0 + jitter)
    area = max(28.0, min(area, 320.0))
    return round(area, 1)


def _random_price(area, layout):
    rooms = _layout_rooms(layout)
    unit = random.uniform(3.0, 7.8)
    if rooms >= 4:
        unit *= random.uniform(1.1, 1.35)
    if rooms == 1:
        unit *= random.uniform(0.85, 1.05)
    price = area * unit
    price = max(80.0, min(price, 2200.0))
    return int(round(price / 10.0) * 10)


def _random_geo(base_lat=39.9042, base_lon=116.4074):
    lat = base_lat + random.uniform(-0.06, 0.06)
    lon = base_lon + random.uniform(-0.10, 0.10)
    return round(lat, 4), round(lon, 4)


def _random_address():
    roads = [
        "人民路", "文昌路", "湖滨西路", "创业大街", "花园路", "银河路", "公园东路", "商业街",
        "建国路", "学院路", "滨河路", "长安街", "朝阳路", "中关村大街", "复兴路", "西直门外大街",
    ]
    suffix = random.choice(["号", "号院", "弄", "号楼"])
    num = random.randint(1, 268)
    return f"{random.choice(roads)}{num}{suffix}"


def _random_community():
    communities = [
        "阳光都市", "翰林世家", "天鹅湖畔", "青年公寓", "四季花城", "星空之城", "都市驿站", "绿野仙踪",
        "梧桐里", "云栖公馆", "锦绣华庭", "蓝湾国际", "中央御景", "雅居乐园", "金茂府", "万科城",
        "融创壹号院", "中海国际", "保利天汇", "龙湖天街", "华润置地", "远洋山水", "世贸天阶", "合生汇",
    ]
    return random.choice(communities)


def _random_tags(layout, price):
    pool = [
        "近地铁", "精装修", "随时看房", "学区房", "满五唯一", "南北通透", "新上", "湖景房",
        "豪华装修", "低总价", "投资回报高", "复式", "带露台", "视野无敌", "拎包入住", "公园房",
        "不限购", "商住两用", "繁华地段", "降价房", "带花园", "人车分流", "电梯房", "得房率高",
        "次新房", "采光好", "安静", "物业好", "品牌开发商",
    ]

    tags = set()
    if "近地铁" in pool and random.random() < 0.45:
        tags.add("近地铁")
    if random.random() < 0.35:
        tags.add("精装修")
    if random.random() < 0.25:
        tags.add("南北通透")
    if _layout_rooms(layout) >= 4 and random.random() < 0.35:
        tags.add("豪华装修")
    if price <= 200 and random.random() < 0.40:
        tags.add("低总价")
    if random.random() < 0.20:
        tags.add("随时看房")

    while len(tags) < random.randint(2, 4):
        tags.add(random.choice(pool))
    return list(tags)


def _random_title(layout, community, tags):
    title_parts = [
        "新上", "稀缺", "品质", "优选", "改善", "投资优选", "地铁口", "公园旁", "商圈核心", "南北通透",
        "视野开阔", "采光充足", "安静舒适", "精装", "带露台", "带花园",
    ]
    rooms = _layout_rooms(layout)
    room_text = {1: "一居", 2: "两居", 3: "三居", 4: "四居", 5: "复式"}.get(rooms, "两居")
    lead = random.choice(title_parts)
    t = f"{lead}{room_text}，{community}，{random.choice(tags)}"
    return t


def _random_description(tags, layout):
    phrases = [
        "户型方正，动静分区合理，居住舒适。",
        "采光充足，通风良好，装修维护到位。",
        "小区配套成熟，生活便利，物业管理规范。",
        "周边交通便捷，商业与休闲设施齐全。",
        "看房方便，欢迎随时预约。",
    ]
    if "学区房" in tags:
        phrases.append("对口学校资源优质，孩子上学更省心。")
    if "近地铁" in tags:
        phrases.append("步行可达地铁站，通勤更高效。")
    if "带露台" in tags:
        phrases.append("赠送超大露台空间，可休闲娱乐。")
    if "带花园" in tags:
        phrases.append("一层带院子，适合有老人孩子的家庭。")
    if _layout_rooms(layout) >= 4:
        phrases.append("适合改善型家庭，空间尺度更从容。")
    random.shuffle(phrases)
    return "".join(phrases[:4])


def generate_more_listings(count=90, filename="mock_listings.json", fetch_images=True):
    with open(filename, 'r+', encoding='utf-8') as f:
        listings = json.load(f)

        start_num = _next_listing_number(listings)
        end_num = start_num + count - 1
        print(f"当前已有 {len(listings)} 条房源，准备新增 {count} 条（R{start_num:03d} - R{end_num:03d}）")

        for n in range(start_num, start_num + count):
            listing_id = f"R{n:03d}"
            layout = _random_layout()
            area = _random_area(layout)
            price = _random_price(area, layout)
            community = _random_community()
            address = _random_address()
            tags = _random_tags(layout, price)
            title = _random_title(layout, community, tags)
            description = _random_description(tags, layout)
            lat, lon = _random_geo()

            image_url = None
            if fetch_images:
                image_url = get_real_estate_image()
            if not image_url:
                image_url = random.choice(listings).get("image_url")

            listings.append(
                {
                    "id": listing_id,
                    "title": title,
                    "community": community,
                    "address": address,
                    "layout": layout,
                    "area": area,
                    "price": price,
                    "tags": tags,
                    "description": description,
                    "image_url": image_url,
                    "latitude": lat,
                    "longitude": lon,
                }
            )

        f.seek(0)
        f.truncate()
        json.dump(listings, f, ensure_ascii=False, indent=2)
        print(f"新增完成，当前总数: {len(listings)}")

if __name__ == "__main__":
    generate_more_listings(count=90, filename="mock_listings.json", fetch_images=False)