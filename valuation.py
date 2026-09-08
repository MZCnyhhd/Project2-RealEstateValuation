"""
房地产全息价值评估引擎
7层 ~160维度，混合推导+确定性模拟，输出综合评分与逐项加减价明细。
"""
import hashlib
import re
from typing import Dict, List, Optional, Any

# === 层级配置 ===
LAYERS = [
    {"id": 1, "name": "宏观经济与城市基本面", "weight": 0.15},
    {"id": 2, "name": "区域与板块价值", "weight": 0.20},
    {"id": 3, "name": "社区与地块品质", "weight": 0.20},
    {"id": 4, "name": "房屋本体", "weight": 0.35},
    {"id": 5, "name": "风水与心理感知", "weight": 0.05},
    {"id": 6, "name": "权益与交易弹性", "weight": 0.05},
    {"id": 7, "name": "特殊事件与历史印记", "weight": 0.01},
]

VERSIONS = {"minimal": 1, "standard": 2, "professional": 3}


def _d(did, layer, cat, name, src, score, ver=2):
    """维度定义工厂。ver: 1=极简, 2=标准, 3=专业"""
    return {"id": did, "layer": layer, "cat": cat, "name": name, "src": src, "score": score, "ver": ver}


# === 维度目录 (~160 dims) ===
# src类型: ("tag",名) | ("desc",词) | ("layout",字段) | ("sim_n",min,max) | ("sim_c",[选项]) | ("const",值) | ("area",) | ("price",)
# score类型: ("bool",正影响,负影响) | ("num",[(阈值,影响),...]降序) | ("cat",{值:影响},默认)
DIMENSIONS = [
    # === Layer 1: 宏观经济与城市基本面 (14 dims, 城市级常量-北京基准) ===
    _d("l1_gdp", 1, "宏观经济", "城市GDP增速", ("const", 5.2), ("num", [(7,3),(6,2),(5,1),(4,0),(3,-1.5),(0,-3)],), 2),
    _d("l1_income", 1, "宏观经济", "人均可支配收入", ("const", 7.5), ("num", [(8,2),(7,1.5),(6,1),(5,0),(4,-1),(0,-2)]), 2),
    _d("l1_pop_flow", 1, "宏观经济", "人口净流入", ("const", 1), ("num", [(1,2),(0.5,1),(0,0),(-0.5,-1.5),(-99,-3)]), 2),
    _d("l1_primary_students", 1, "宏观经济", "小学生人数变化", ("const", 3.5), ("num", [(5,2),(3,1),(1,0),(-1,-1),(-99,-2)]), 3),
    _d("l1_listed_cos", 1, "宏观经济", "上市企业数量", ("const", 80), ("num", [(100,2),(60,1.5),(30,1),(10,0),(0,-1)]), 3),
    _d("l1_private_pct", 1, "宏观经济", "民营经济占比", ("const", 40), ("num", [(60,2),(50,1),(40,0),(30,-1),(0,-2)]), 3),
    _d("l1_deposits", 1, "宏观经济", "金融机构存款总量", ("const", 90), ("num", [(90,2),(70,1.5),(50,1),(30,0),(0,-1.5)]), 2),
    _d("l1_land_finance", 1, "宏观经济", "土地财政依赖度", ("const", 35), ("num", [(20,1),(35,0),(50,-1.5),(70,-3),(100,-4)]), 3),
    _d("l1_mega_projects", 1, "宏观经济", "五年规划重大项目", ("const", 8), ("num", [(10,3),(6,2),(3,1),(1,0),(0,-1)]), 2),
    _d("l1_aging", 1, "宏观经济", "城市老龄化率", ("const", 14), ("num", [(10,1),(14,0),(18,-1.5),(22,-3),(30,-4)]), 3),
    _d("l1_young_idx", 1, "宏观经济", "城市年轻指数", ("const", 38), ("num", [(45,2),(38,1),(30,0),(25,-1),(0,-2)]), 2),
    _d("l1_air_quality", 1, "宏观经济", "空气质量PM2.5", ("const", 42), ("num", [(25,2),(35,1),(50,0),(75,-1.5),(100,-3)]), 2),
    _d("l1_metro_plan", 1, "宏观经济", "地铁规划里程", ("const", 1.5), ("num", [(2,3),(1.5,2),(1,1),(0.5,0),(0,-1)]), 2),
    _d("l1_primacy", 1, "宏观经济", "城市首位度", ("const", 2.5), ("num", [(3,2),(2.5,1.5),(2,1),(1.5,0),(0,-1)]), 3),

    # === Layer 2: 区域与板块价值 (30 dims) ===
    # 产业支撑
    _d("l2_job_density", 2, "产业支撑", "周边就业人口密度", ("sim_n", 0.5, 3.0), ("num", [(2.5,3),(2,2),(1.5,1),(1,0),(0.5,-1),(0,-2)]), 2),
    _d("l2_high_salary", 2, "产业支撑", "高薪行业占比", ("sim_n", 10, 50), ("num", [(40,3),(30,2),(20,1),(15,0),(0,-1.5)]), 3),
    _d("l2_vacancy", 2, "产业支撑", "产业园区空置率", ("sim_n", 5, 35), ("num", [(10,2),(15,1),(20,0),(30,-2),(35,-3)]), 3),
    _d("l2_dev_axis", 2, "产业支撑", "城市发展主轴", ("sim_c", ["是", "否"]), ("cat", {"是": 3, "否": 0}, 0), 2),
    # 交通网络
    _d("l2_subway_dist", 2, "交通网络", "地铁站步行距离", ("tag", "近地铁"), ("bool", 5, -1), 1),
    _d("l2_cbd_commute", 2, "交通网络", "距CBD通勤时间", ("sim_n", 15, 75), ("num", [(20,4),(30,2),(40,0),(50,-2),(60,-3),(75,-5)]), 2),
    _d("l2_transfer_stn", 2, "交通网络", "是否换乘站", ("sim_c", ["换乘站", "普通站", "无地铁"]), ("cat", {"换乘站": 3, "普通站": 1, "无地铁": -3}, 0), 3),
    _d("l2_crowd_idx", 2, "交通网络", "早高峰拥挤指数", ("sim_n", 3, 10), ("num", [(4,2),(5,1),(6,0),(7,-1),(8,-3),(10,-5)]), 3),
    _d("l2_highway", 2, "交通网络", "上快速路时间", ("sim_n", 1, 15), ("num", [(3,2),(5,1),(8,0),(12,-1),(15,-2)]), 3),
    _d("l2_bus_lines", 2, "交通网络", "公交线路数量", ("sim_n", 2, 20), ("num", [(15,2),(10,1.5),(6,1),(3,0),(0,-1)]), 3),
    _d("l2_bike_share", 2, "交通网络", "共享单车停放区", ("sim_c", ["有", "无"]), ("cat", {"有": 0.5, "无": 0}, 0), 3),
    _d("l2_desc_subway", 2, "交通网络", "描述提及地铁便利", ("desc", "步行可达地铁站"), ("bool", 2, 0), 2),
    # 教育资源
    _d("l2_school", 2, "教育资源", "对口小学评级", ("tag", "学区房"), ("bool", 12, 0), 1),
    _d("l2_middle_school", 2, "教育资源", "对口中学升学率", ("sim_n", 30, 90), ("num", [(80,5),(60,3),(50,1),(40,0),(30,-2)]), 3),
    _d("l2_kindergarten", 2, "教育资源", "幼儿园距离/等级", ("sim_n", 200, 2000), ("num", [(300,3),(500,2),(800,1),(1200,0),(2000,-1)]), 3),
    _d("l2_training", 2, "教育资源", "培训机构密度", ("sim_n", 0, 10), ("num", [(8,2),(5,1),(3,0.5),(0,0)]), 3),
    _d("l2_school_policy", 2, "教育资源", "学区政策稳定性", ("sim_c", ["单校划片", "多校划片"]), ("cat", {"单校划片": 2, "多校划片": -3}, 0), 2),
    _d("l2_desc_school", 2, "教育资源", "描述提及优质学校", ("desc", "对口学校资源优质"), ("bool", 3, 0), 2),
    # 商业医疗
    _d("l2_mall", 2, "商业医疗", "大型商超距离", ("sim_n", 200, 3000), ("num", [(500,3),(1000,2),(1500,1),(2000,0),(3000,-2)]), 2),
    _d("l2_street_biz", 2, "商业医疗", "社区底商完整度", ("sim_n", 3, 15), ("num", [(12,2),(8,1.5),(5,1),(3,0)]), 3),
    _d("l2_hospital", 2, "商业医疗", "三甲医院车程", ("sim_n", 5, 40), ("num", [(10,2),(15,1.5),(20,1),(30,0),(40,-1)]), 2),
    _d("l2_commercial_st", 2, "商业医疗", "特色商业街", ("tag", "繁华地段"), ("bool", 3, 0), 2),
    # 生态环境
    _d("l2_park", 2, "生态环境", "公园步行可达性", ("tag", "公园房"), ("bool", 4, 0), 1),
    _d("l2_water_view", 2, "生态环境", "河流湖泊景观", ("tag", "湖景房"), ("bool", 8, 0), 1),
    _d("l2_micro_air", 2, "生态环境", "微环境空气质量", ("sim_n", 50, 200), ("num", [(150,2),(100,1),(50,0)]), 3),
    _d("l2_noise", 2, "生态环境", "噪音污染", ("tag", "安静"), ("bool", 3, 0), 2),
    _d("l2_light_pollution", 2, "生态环境", "光污染", ("sim_c", ["无", "轻微", "严重"]), ("cat", {"无": 1, "轻微": 0, "严重": -2}, 0), 3),
    # 社会与心理
    _d("l2_taboo_1km", 2, "社会心理", "周边忌讳设施(1km)", ("sim_c", ["无", "殡仪馆", "墓地"]), ("cat", {"无": 0, "殡仪馆": -15, "墓地": -10}, 0), 2),
    _d("l2_substation", 2, "社会心理", "周边变电站/高压线", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -5}, 0), 3),
    _d("l2_garbage", 2, "社会心理", "垃圾中转站/污水处理", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -8}, 0), 3),
    _d("l2_safety", 2, "社会心理", "周边治安案发率", ("sim_n", 20, 100), ("num", [(30,2),(50,1),(70,0),(85,-2),(100,-4)]), 3),
    # 城市更新
    _d("l2_renewal", 2, "城市更新", "旧改/棚改范围", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": 2}, 0), 3),
    _d("l2_new_highway", 2, "城市更新", "新建高架规划", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -3}, 0), 3),

    # === Layer 3: 社区与地块品质 (28 dims) ===
    _d("l3_far", 3, "小区硬指标", "容积率", ("sim_n", 1.2, 4.5), ("num", [(1.5,4),(2.0,3),(2.5,2),(3.0,1),(3.5,0),(4.0,-2),(4.5,-4)]), 2),
    _d("l3_green", 3, "小区硬指标", "绿化率", ("sim_n", 20, 55), ("num", [(50,3),(40,2),(35,1),(30,0),(25,-1),(20,-2)]), 2),
    _d("l3_build_year", 3, "小区硬指标", "建筑年代", ("tag", "次新房"), ("bool", 4, 0), 1),
    _d("l3_build_age", 3, "小区硬指标", "房龄折损", ("sim_n", 1, 30), ("num", [(3,3),(5,2),(10,1),(15,0),(20,-2),(25,-3),(30,-5)]), 2),
    _d("l3_units", 3, "小区硬指标", "总户数合理性", ("sim_n", 80, 3500), ("num", [(500,2),(800,3),(1200,2),(1500,1),(2000,0),(3000,-2),(3500,-3)]), 3),
    _d("l3_ped_car", 3, "小区硬指标", "人车分流", ("tag", "人车分流"), ("bool", 5, 0), 1),
    _d("l3_parking", 3, "小区硬指标", "车位配比", ("sim_n", 0.4, 1.5), ("num", [(1.2,3),(1.0,2),(0.8,0),(0.6,-2),(0.4,-3)]), 2),
    _d("l3_pure_residential", 3, "小区硬指标", "是否纯住宅", ("tag", "商住两用"), ("bool", -5, 2), 2),
    # 楼栋位置
    _d("l3_gate_dist", 3, "楼栋位置", "距小区大门距离", ("sim_n", 50, 600), ("num", [(100,2),(200,1),(350,0),(500,-1),(600,-2)]), 3),
    _d("l3_road_noise", 3, "楼栋位置", "临主干道/高架", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -6}, 0), 2),
    _d("l3_center_pos", 3, "楼栋位置", "楼王位置", ("sim_c", ["中心", "边缘", "普通"]), ("cat", {"中心": 6, "普通": 0, "边缘": -2}, 0), 3),
    _d("l3_building_gap", 3, "楼栋位置", "前后楼间距", ("sim_n", 15, 80), ("num", [(60,3),(45,2),(35,1),(30,0),(25,-2),(15,-4)]), 2),
    # 物业管理
    _d("l3_property_fee", 3, "物业管理", "物业费水平", ("sim_n", 1.0, 9.0), ("num", [(2.5,1),(3.5,2),(5.0,1.5),(7.0,0.5),(9.0,-0.5)]), 2),
    _d("l3_property_brand", 3, "物业管理", "物业品牌", ("tag", "物业好"), ("bool", 5, 0), 1),
    _d("l3_brand_dev", 3, "物业管理", "品牌开发商", ("tag", "品牌开发商"), ("bool", 4, 0), 2),
    _d("l3_security", 3, "物业管理", "保安状态", ("sim_n", 40, 95), ("num", [(85,2),(70,1),(55,0),(40,-1)]), 3),
    _d("l3_cleanliness", 3, "物业管理", "电梯楼道清洁度", ("sim_n", 50, 100), ("num", [(90,2),(75,1),(60,0),(50,-2)]), 3),
    _d("l3_green_maintain", 3, "物业管理", "绿化维护状况", ("sim_n", 40, 100), ("num", [(85,2),(70,1),(55,0),(40,-2)]), 3),
    _d("l3_repair_time", 3, "物业管理", "报修响应时间", ("sim_n", 2, 72), ("num", [(4,2),(8,1),(24,0),(48,-1),(72,-2)]), 3),
    _d("l3_desc_property", 3, "物业管理", "描述提及物业规范", ("desc", "物业管理规范"), ("bool", 2, 0), 2),
    # 社区配套
    _d("l3_playground", 3, "社区配套", "儿童游乐场", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 3),
    _d("l3_gym", 3, "社区配套", "健身活动区", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l3_pool", 3, "社区配套", "游泳池/会所", ("sim_c", ["有", "无"]), ("cat", {"有": 3, "无": 0}, 0), 3),
    _d("l3_charging", 3, "社区配套", "电瓶车充电桩", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l3_express", 3, "社区配套", "快递柜/驿站", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l3_desc_facility", 3, "社区配套", "描述提及配套成熟", ("desc", "小区配套成熟"), ("bool", 2, 0), 2),
    # 社区氛围
    _d("l3_rental_rate", 3, "社区氛围", "出租率", ("sim_n", 5, 50), ("num", [(15,2),(25,1),(30,0),(40,-2),(50,-3)]), 2),
    _d("l3_occupancy", 3, "社区氛围", "入住率/亮灯率", ("sim_n", 40, 98), ("num", [(90,2),(80,1),(70,0),(60,-2),(40,-5)]), 3),

    # === Layer 4: 房屋本体 (50 dims) ===
    # 4.1 建筑结构
    _d("l4_structure", 4, "建筑结构", "结构类型", ("sim_c", ["钢结构", "框架", "砖混"]), ("cat", {"钢结构": 5, "框架": 2, "砖混": -3}, 0), 2),
    _d("l4_facade", 4, "建筑结构", "外墙材质", ("sim_c", ["石材", "真石漆", "涂料"]), ("cat", {"石材": 3, "真石漆": 1, "涂料": -1}, 0), 3),
    _d("l4_insulation", 4, "建筑结构", "外墙保温层", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l4_roof", 4, "建筑结构", "屋顶形式", ("sim_c", ["坡顶", "平顶"]), ("cat", {"坡顶": 2, "平顶": 0}, 0), 3),
    _d("l4_foundation", 4, "建筑结构", "地基状况", ("sim_c", ["正常", "轻微裂缝", "沉降"]), ("cat", {"正常": 0, "轻微裂缝": -8, "沉降": -20}, 0), 2),
    _d("l4_duplex", 4, "建筑结构", "复式结构", ("tag", "复式"), ("bool", 4, 0), 2),
    # 4.2 楼层与垂直交通
    _d("l4_floor_pos", 4, "楼层交通", "楼层位置评分", ("sim_n", 1, 33), ("num", [(3,-3),(5,-1),(8,1),(12,3),(18,5),(24,4),(28,2),(31,0),(33,-3)]), 1),
    _d("l4_top_floor", 4, "楼层交通", "是否顶层", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -4}, 0), 2),
    _d("l4_first_floor", 4, "楼层交通", "是否一层", ("desc", "一层带院子"), ("bool", -2, 0), 2),
    _d("l4_elevator", 4, "楼层交通", "电梯配置", ("tag", "电梯房"), ("bool", 3, -1), 1),
    _d("l4_elevator_brand", 4, "楼层交通", "电梯品牌", ("sim_c", ["三菱", "日立", "通力", "杂牌"]), ("cat", {"三菱": 1, "日立": 1, "通力": 0.5, "杂牌": -2}, 0), 3),
    _d("l4_elevator_ratio", 4, "楼层交通", "梯户比", ("sim_c", ["1T2", "2T4", "2T6", "3T8"]), ("cat", {"1T2": 3, "2T4": 1.5, "2T6": 0, "3T8": -2}, 0), 2),
    _d("l4_elevator_garage", 4, "楼层交通", "电梯直达车库", ("sim_c", ["是", "否"]), ("cat", {"是": 1, "否": 0}, 0), 3),
    # 4.3 户型布局
    _d("l4_squareness", 4, "户型布局", "户型方正度", ("desc", "户型方正"), ("bool", 4, 0), 1),
    _d("l4_north_south", 4, "户型布局", "南北通透", ("tag", "南北通透"), ("bool", 5, 0), 1),
    _d("l4_dynamic_static", 4, "户型布局", "动静分区", ("desc", "动静分区"), ("bool", 2, 0), 2),
    _d("l4_clean_dirty", 4, "户型布局", "洁污分区", ("sim_c", ["好", "一般", "差"]), ("cat", {"好": 1, "一般": 0, "差": -1}, 0), 3),
    _d("l4_width_depth", 4, "户型布局", "面宽进深比", ("sim_n", 0.6, 1.5), ("num", [(1.3,3),(1.1,2),(0.9,1),(0.8,0),(0.6,-2)]), 3),
    _d("l4_living_width", 4, "户型布局", "客厅开间", ("sim_n", 2.8, 5.5), ("num", [(4.5,3),(4.0,2),(3.6,1),(3.3,0),(3.0,-2),(2.8,-3)]), 2),
    _d("l4_master_width", 4, "户型布局", "主卧开间", ("sim_n", 2.6, 4.5), ("num", [(4.0,3),(3.6,2),(3.3,1),(3.0,0),(2.6,-2)]), 2),
    _d("l4_living_balcony", 4, "户型布局", "客厅带阳台", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 2),
    _d("l4_master_bath", 4, "户型布局", "主卧独立卫浴", ("layout", "baths_gte_2"), ("bool", 3, 0), 2),
    _d("l4_walkin_closet", 4, "户型布局", "主卧衣帽间", ("sim_c", ["有", "无"]), ("cat", {"有": 3, "无": 0}, 0), 3),
    _d("l4_kitchen_type", 4, "户型布局", "厨房操作台", ("sim_c", ["U型", "L型", "一字型"]), ("cat", {"U型": 2, "L型": 1, "一字型": 0}, 0), 3),
    _d("l4_bright_kitchen", 4, "户型布局", "明厨", ("sim_c", ["有窗", "无窗"]), ("cat", {"有窗": 1, "无窗": 0}, 0), 3),
    _d("l4_bright_bath", 4, "户型布局", "明卫", ("sim_c", ["有窗", "无窗"]), ("cat", {"有窗": 2.5, "无窗": -1}, 0), 2),
    _d("l4_dry_wet", 4, "户型布局", "干湿分离", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 2),
    _d("l4_dining", 4, "户型布局", "独立餐厅", ("layout", "halls_gte_1"), ("bool", 1, -0.5), 3),
    _d("l4_entrance", 4, "户型布局", "玄关", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l4_storage", 4, "户型布局", "储藏间", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 3),
    _d("l4_balcony_count", 4, "户型布局", "阳台数量", ("tag", "带露台"), ("bool", 3, 0), 2),
    _d("l4_balcony_depth", 4, "户型布局", "阳台进深", ("sim_n", 0.8, 2.5), ("num", [(2.0,2),(1.5,1),(1.2,0),(0.8,-1)]), 3),
    _d("l4_bay_window", 4, "户型布局", "飘窗", ("sim_c", ["可砸", "不可砸", "无"]), ("cat", {"可砸": 1.5, "不可砸": 0.5, "无": 0}, 0), 3),
    _d("l4_ceiling", 4, "户型布局", "层高(净高)", ("sim_n", 2.4, 3.2), ("num", [(3.0,3),(2.8,2),(2.7,1),(2.6,0),(2.5,-2),(2.4,-3)]), 2),
    _d("l4_corridor_pct", 4, "户型布局", "过道面积占比", ("sim_n", 1, 12), ("num", [(3,1),(5,0),(8,-1),(12,-2)]), 3),
    _d("l4_usable_rate", 4, "户型布局", "得房率", ("tag", "得房率高"), ("bool", 3, 0), 2),
    _d("l4_garden", 4, "户型布局", "带花园", ("tag", "带花园"), ("bool", 4, 0), 2),
    # 4.4 采光与通风
    _d("l4_living_orient", 4, "采光通风", "客厅朝向", ("sim_c", ["南", "东南", "西南", "东", "西", "北"]), ("cat", {"南": 5, "东南": 3, "西南": 1, "东": 0, "西": -2, "北": -4}, 0), 2),
    _d("l4_master_orient", 4, "采光通风", "主卧朝向", ("sim_c", ["南", "东南", "西南", "东", "西", "北"]), ("cat", {"南": 4, "东南": 3, "西南": 1, "东": 0, "西": -1.5, "北": -3}, 0), 2),
    _d("l4_dark_rooms", 4, "采光通风", "暗房数量", ("sim_n", 0, 3), ("num", [(0,2),(1,0),(2,-3),(3,-5)]), 2),
    _d("l4_window_ratio", 4, "采光通风", "窗地比", ("sim_n", 0.08, 0.25), ("num", [(0.2,2),(0.15,1),(0.125,0),(0.1,-1),(0.08,-2)]), 3),
    _d("l4_window_type", 4, "采光通风", "窗户类型", ("sim_c", ["断桥铝", "塑钢", "铝合金"]), ("cat", {"断桥铝": 1, "塑钢": 0, "铝合金": -1}, 0), 3),
    _d("l4_obstruction", 4, "采光通风", "窗外永久遮挡", ("sim_c", ["无", "轻微", "严重"]), ("cat", {"无": 0, "轻微": -4, "严重": -8}, 0), 2),
    _d("l4_west_sun", 4, "采光通风", "西晒", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -1.5}, 0), 3),
    _d("l4_tag_light", 4, "采光通风", "采光好标签", ("tag", "采光好"), ("bool", 3, 0), 1),
    _d("l4_desc_light", 4, "采光通风", "描述提及采光充足", ("desc", "采光充足"), ("bool", 2, 0), 2),
    _d("l4_ventilation", 4, "采光通风", "通风良好", ("desc", "通风良好"), ("bool", 1.5, 0), 2),
    # 4.5 室内设备
    _d("l4_heating", 4, "室内设备", "供暖方式", ("sim_c", ["集中供暖", "地暖", "自采暖", "无"]), ("cat", {"集中供暖": 2, "地暖": 2.5, "自采暖": 0, "无": -2}, 0), 2),
    _d("l4_central_ac", 4, "室内设备", "中央空调", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 2),
    _d("l4_fresh_air", 4, "室内设备", "新风系统", ("sim_c", ["有", "无"]), ("cat", {"有": 2, "无": 0}, 0), 3),
    _d("l4_water_purify", 4, "室内设备", "净水/软水系统", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l4_gas", 4, "室内设备", "管道天然气", ("sim_c", ["有", "液化气"]), ("cat", {"有": 1, "液化气": -0.5}, 0), 3),
    _d("l4_fiber", 4, "室内设备", "光纤千兆", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l4_electric_cap", 4, "室内设备", "电路容量", ("sim_c", ["充足", "一般", "不足"]), ("cat", {"充足": 1, "一般": 0, "不足": -1}, 0), 3),
    # 4.6 装修与维护
    _d("l4_deco_level", 4, "装修维护", "装修等级", ("tag", "精装修"), ("bool", 4, 0), 1),
    _d("l4_deco_luxury", 4, "装修维护", "豪华装修", ("tag", "豪华装修"), ("bool", 6, 0), 1),
    _d("l4_deco_age", 4, "装修维护", "装修年代", ("sim_n", 0, 15), ("num", [(2,3),(4,2),(6,1),(8,0),(10,-2),(15,-4)]), 2),
    _d("l4_deco_style", 4, "装修维护", "装修风格现代度", ("sim_n", 30, 100), ("num", [(85,2),(70,1),(50,0),(30,-1.5)]), 3),
    _d("l4_floor_material", 4, "装修维护", "地面材质", ("sim_c", ["实木", "复合", "瓷砖"]), ("cat", {"实木": 1.5, "复合": 0.5, "瓷砖": -0.5}, 0), 3),
    _d("l4_wall_state", 4, "装修维护", "墙面状态", ("sim_c", ["良好", "轻微老化", "脱落"]), ("cat", {"良好": 1, "轻微老化": -1, "脱落": -2}, 0), 3),
    _d("l4_bath_brand", 4, "装修维护", "卫浴品牌", ("sim_c", ["TOTO", "科勒", "普通", "杂牌"]), ("cat", {"TOTO": 1.5, "科勒": 1, "普通": 0, "杂牌": -1}, 0), 3),
    _d("l4_smart_toilet", 4, "装修维护", "智能马桶", ("sim_c", ["有", "无"]), ("cat", {"有": 1, "无": 0}, 0), 3),
    _d("l4_glass", 4, "装修维护", "窗户玻璃", ("sim_c", ["三层中空", "双层中空", "单层"]), ("cat", {"三层中空": 1.5, "双层中空": 0.5, "单层": -2}, 0), 3),
    _d("l4_door_grade", 4, "装修维护", "入户门等级", ("sim_c", ["甲级", "乙级", "普通"]), ("cat", {"甲级": 1, "乙级": 0.5, "普通": -0.5}, 0), 3),
    _d("l4_leak", 4, "装修维护", "漏水/渗水痕迹", ("sim_c", ["无", "轻微", "严重"]), ("cat", {"无": 0, "轻微": -5, "严重": -10}, 0), 2),
    _d("l4_crack", 4, "装修维护", "结构性裂缝", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -20}, 0), 2),
    _d("l4_move_in", 4, "装修维护", "拎包入住", ("tag", "拎包入住"), ("bool", 2, 0), 2),
    _d("l4_desc_deco", 4, "装修维护", "描述提及装修维护", ("desc", "装修维护到位"), ("bool", 1.5, 0), 2),
    # 4.7 室外与视线
    _d("l4_view_type", 4, "室外视线", "窗外景观类型", ("tag", "视野无敌"), ("bool", 8, 0), 1),
    _d("l4_view_block", 4, "室外视线", "视线遮挡", ("sim_c", ["无遮挡", "部分遮挡", "严重遮挡"]), ("cat", {"无遮挡": 2, "部分遮挡": -2, "严重遮挡": -5}, 0), 2),
    _d("l4_drying", 4, "室外视线", "晾晒条件", ("sim_c", ["阳台", "外置晾架", "无"]), ("cat", {"阳台": 1, "外置晾架": 0, "无": -2}, 0), 3),
    _d("l4_ac_unit", 4, "室外视线", "空调外机规整度", ("sim_c", ["规整", "凌乱"]), ("cat", {"规整": 0.5, "凌乱": -1}, 0), 3),

    # === Layer 5: 风水与心理感知 (14 dims) ===
    _d("l5_haunted_self", 5, "凶宅效应", "本户凶宅", ("sim_c", ["否", "否", "否", "否", "否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": -25}, 0), 1),
    _d("l5_haunted_floor", 5, "凶宅效应", "同层凶宅", ("sim_c", ["否", "否", "否", "否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": -9}, 0), 2),
    _d("l5_haunted_building", 5, "凶宅效应", "同栋凶宅", ("sim_c", ["否", "否", "否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": -6}, 0), 3),
    _d("l5_haunted_community", 5, "凶宅效应", "同小区凶宅", ("sim_c", ["否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": -1}, 0), 3),
    _d("l5_bow_road", 5, "地理风水", "反弓路", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -7}, 0), 2),
    _d("l5_road_rush", 5, "地理风水", "路冲", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -15}, 0), 2),
    _d("l5_sky_cut", 5, "地理风水", "天斩煞", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -7}, 0), 3),
    _d("l5_embrace", 5, "地理风水", "环抱水/路", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": 4}, 0), 3),
    _d("l5_terrain", 5, "地理风水", "地势前高后低", ("sim_c", ["正常", "前高后低"]), ("cat", {"正常": 0, "前高后低": -4}, 0), 3),
    _d("l5_funeral_near", 5, "地理风水", "近殡仪馆/墓地", ("sim_c", ["远", "近"]), ("cat", {"远": 0, "近": -12}, 0), 2),
    _d("l5_door_toilet", 5, "室内风水", "入门见厕", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -4}, 0), 2),
    _d("l5_beam", 5, "室内风水", "横梁压顶", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -2.5}, 0), 3),
    _d("l5_through", 5, "室内风水", "穿堂煞", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -3}, 0), 3),
    _d("l5_number4", 5, "文化忌讳", "门牌号含4", ("sim_c", ["否", "否", "否", "是"]), ("cat", {"否": 0, "是": -1.5}, 0), 3),

    # === Layer 6: 权益与交易弹性 (16 dims) ===
    _d("l6_property_type", 6, "产权性质", "产权类型", ("tag", "不限购"), ("bool", -3, 2), 1),
    _d("l6_land_type", 6, "产权性质", "土地性质", ("sim_c", ["出让", "划拨"]), ("cat", {"出让": 0, "划拨": -5}, 0), 2),
    _d("l6_land_years", 6, "产权性质", "土地剩余年限", ("sim_n", 35, 70), ("num", [(65,1),(55,0),(45,-1.5),(35,-3)]), 2),
    _d("l6_co_owners", 6, "产权性质", "多人共有", ("sim_c", ["单人", "多人"]), ("cat", {"单人": 0, "多人": -2}, 0), 3),
    _d("l6_mortgage", 6, "权利限制", "银行抵押", ("sim_c", ["无", "有"]), ("cat", {"无": 1, "有": -0.5}, 0), 3),
    _d("l6_sealed", 6, "权利限制", "查封/冻结", ("sim_c", ["否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": -35}, 0), 2),
    _d("l6_lease", 6, "权利限制", "长期租约", ("desc", "带租约出售"), ("bool", -8, 0), 2),
    _d("l6_residence_right", 6, "权利限制", "居住权登记", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -30}, 0), 3),
    _d("l6_hukou", 6, "权利限制", "户口迁出状况", ("sim_c", ["已迁", "未迁"]), ("cat", {"已迁": 0, "未迁": -5}, 0), 2),
    _d("l6_tax_5y", 6, "税费成本", "满五唯一", ("tag", "满五唯一"), ("bool", 4, 0), 1),
    _d("l6_tax_2y", 6, "税费成本", "满二", ("sim_c", ["满五", "满二不满五", "不满二"]), ("cat", {"满五": 0, "满二不满五": -2, "不满二": -5}, 0), 2),
    _d("l6_inherit", 6, "税费成本", "继承/赠与所得", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -7}, 0), 3),
    _d("l6_fee_owed", 6, "税费成本", "物业费欠缴", ("sim_c", ["无", "有"]), ("cat", {"无": 0, "有": -1}, 0), 3),
    _d("l6_urgent_sell", 6, "交易条件", "卖家急售", ("tag", "降价房"), ("bool", 5, 0), 1),
    _d("l6_listing_days", 6, "交易条件", "挂牌天数", ("sim_n", 3, 300), ("num", [(30,2),(60,1),(120,0),(180,-2),(300,-4)]), 2),
    _d("l6_price_cuts", 6, "交易条件", "降价次数", ("sim_n", 0, 5), ("num", [(0,1),(1,0.5),(2,0),(3,-2),(5,-4)]), 3),

    # === Layer 7: 特殊事件与历史印记 (5 dims) ===
    _d("l7_celebrity", 7, "历史印记", "名人曾居住", ("sim_c", ["否", "否", "否", "否", "否", "否", "否", "否", "否", "是"]), ("cat", {"否": 0, "是": 7}, 0), 2),
    _d("l7_heritage", 7, "历史印记", "历史保护建筑", ("tag", "历史建筑"), ("bool", 4, 0), 2),
    _d("l7_commercial_use", 7, "历史印记", "曾作商业用途", ("sim_c", ["否", "是"]), ("cat", {"否": 0, "是": -2}, 0), 3),
    _d("l7_news_event", 7, "历史印记", "重大新闻事件", ("sim_c", ["无", "正面", "负面"]), ("cat", {"无": 0, "正面": 3, "负面": -5}, 0), 3),
    _d("l7_neighbor_quality", 7, "历史印记", "邻居圈层", ("tag", "有故事的房子"), ("bool", 2, 0), 2),
]

# === Tag 对楼层的覆盖修正 ===
_TAG_FLOOR_OVERRIDE = {
    "电梯房": (6, 28),
    "复式": (15, 33),
}


# === 确定性模拟 ===
def _hash_seed(listing_id: str, dim_id: str) -> int:
    """基于listing_id + dim_id的确定性hash"""
    h = hashlib.md5(f"{listing_id}:{dim_id}".encode()).hexdigest()
    return int(h[:8], 16)


def _sim_numeric(listing_id: str, dim_id: str, min_val: float, max_val: float) -> float:
    """确定性模拟数值"""
    seed = _hash_seed(listing_id, dim_id)
    ratio = (seed % 10000) / 10000.0
    return round(min_val + ratio * (max_val - min_val), 2)


def _sim_categorical(listing_id: str, dim_id: str, choices: list) -> str:
    """确定性模拟分类值"""
    seed = _hash_seed(listing_id, dim_id)
    return choices[seed % len(choices)]


# === 推导引擎 ===
def _parse_layout(layout: str) -> Dict[str, int]:
    """解析户型字符串 '3室2厅2卫' → {rooms:3, halls:2, baths:2}"""
    m = re.match(r"(\d+)室(\d*)厅?(\d*)卫?", layout or "")
    if m:
        return {
            "rooms": int(m.group(1)),
            "halls": int(m.group(2)) if m.group(2) else 0,
            "baths": int(m.group(3)) if m.group(3) else 1,
        }
    if "开放式" in (layout or ""):
        return {"rooms": 1, "halls": 0, "baths": 1}
    return {"rooms": 2, "halls": 1, "baths": 1}


def derive_listing_dimensions(listing: dict) -> Dict[str, Any]:
    """推导/模拟所有维度的原始值"""
    lid = listing.get("id", "R000")
    tags = set(listing.get("tags", []))
    desc = listing.get("description", "")
    layout_info = _parse_layout(listing.get("layout", ""))
    values = {}

    for dim in DIMENSIONS:
        did = dim["id"]
        src = dim["src"]
        src_type = src[0]

        if src_type == "tag":
            tag_name = src[1]
            values[did] = tag_name in tags

        elif src_type == "desc":
            keyword = src[1]
            values[did] = keyword in desc

        elif src_type == "layout":
            field = src[1]
            if field == "baths_gte_2":
                values[did] = layout_info["baths"] >= 2
            elif field == "halls_gte_1":
                values[did] = layout_info["halls"] >= 1
            elif field == "rooms":
                values[did] = layout_info["rooms"]
            else:
                values[did] = 0

        elif src_type == "sim_n":
            min_v, max_v = src[1], src[2]
            # 特殊覆盖：电梯房tag调整楼层范围
            if did == "l4_floor_pos" and "电梯房" in tags:
                val = _sim_numeric(lid, did, 6, 28)
            elif did == "l4_floor_pos" and "电梯房" not in tags:
                val = _sim_numeric(lid, did, 1, 7)
            else:
                val = _sim_numeric(lid, did, min_v, max_v)
            values[did] = val

        elif src_type == "sim_c":
            choices = src[1]
            val = _sim_categorical(lid, did, choices)
            # 南北通透覆盖朝向
            if did == "l4_living_orient" and "南北通透" in tags:
                val = "南"
            elif did == "l4_master_orient" and "南北通透" in tags:
                val = "南"
            values[did] = val

        elif src_type == "const":
            values[did] = src[1]

        elif src_type == "area":
            values[did] = listing.get("area", 90)

        elif src_type == "price":
            values[did] = listing.get("price", 300)

        else:
            values[did] = 0

    return values


# === 评分引擎 ===
def _score_dimension(dim: dict, raw_value: Any) -> tuple:
    """对单个维度评分，返回 (impact_pct, display_value)"""
    score_cfg = dim["score"]
    score_type = score_cfg[0]

    if score_type == "bool":
        impact_true = score_cfg[1]
        impact_false = score_cfg[2] if len(score_cfg) > 2 else 0
        if isinstance(raw_value, bool):
            impact = impact_true if raw_value else impact_false
            display = "是" if raw_value else "否"
        else:
            impact = impact_true if raw_value else impact_false
            display = str(raw_value)
        return impact, display

    elif score_type == "num":
        thresholds = sorted(score_cfg[1], key=lambda x: -x[0])  # 确保降序匹配
        try:
            val = float(raw_value)
        except (TypeError, ValueError):
            return 0, str(raw_value)
        # 找到第一个 val >= threshold 的区间
        impact = thresholds[-1][1] if thresholds else 0
        for threshold, imp in thresholds:
            if val >= threshold:
                impact = imp
                break
        return impact, f"{val:.1f}" if isinstance(raw_value, float) else str(raw_value)

    elif score_type == "cat":
        mapping = score_cfg[1]
        default = score_cfg[2] if len(score_cfg) > 2 else 0
        impact = mapping.get(str(raw_value), default)
        return impact, str(raw_value)

    return 0, str(raw_value)


# === 市场基线 ===
def _compute_market_baseline(listing: dict, all_listings: list) -> float:
    """同小区均价×面积 作为市场基线（万元）"""
    if not all_listings:
        return listing.get("price", 300)
    community = listing.get("community", "")
    same = [l for l in all_listings if l.get("community") == community and l.get("area", 0) > 0]
    if len(same) < 2:
        same = [l for l in all_listings if l.get("area", 0) > 0]
    if not same:
        return listing.get("price", 300)
    avg_unit = sum(l["price"] / l["area"] for l in same) / len(same)
    return round(avg_unit * listing.get("area", 90), 1)


# === 主评估入口 ===
def evaluate_listing(listing: dict, all_listings: list = None, version: str = "standard") -> dict:
    """
    全息估值主入口。
    返回包含评分、雷达图数据、逐项加减价、估值结论的完整字典。
    """
    ver_level = VERSIONS.get(version, 2)
    # 过滤维度
    active_dims = [d for d in DIMENSIONS if d["ver"] <= ver_level]

    # 推导所有维度值
    raw_values = derive_listing_dimensions(listing)

    # 评分
    all_factors = []
    layer_impacts = {layer["id"]: [] for layer in LAYERS}
    net_impact_pct = 0.0

    for dim in active_dims:
        did = dim["id"]
        raw_val = raw_values.get(did)
        impact, display = _score_dimension(dim, raw_val)
        net_impact_pct += impact
        layer_impacts[dim["layer"]].append(impact)
        all_factors.append({
            "id": did,
            "name": dim["name"],
            "layer": dim["layer"],
            "layer_name": next((l["name"] for l in LAYERS if l["id"] == dim["layer"]), ""),
            "category": dim["cat"],
            "impact_pct": round(impact, 2),
            "display_value": display,
        })

    # 计算折合万元
    listing_price = listing.get("price", 300)
    for f in all_factors:
        f["impact_wan"] = round(listing_price * f["impact_pct"] / 100.0, 1)

    # 层级评分 (0-100)
    layer_results = []
    for layer in LAYERS:
        lid = layer["id"]
        impacts = layer_impacts[lid]
        dims_in_layer = [d for d in active_dims if d["layer"] == lid]
        # 层得分: 50基准 + 该层净影响(归一化)
        if impacts:
            max_possible = sum(abs(d["score"][1] if d["score"][0] == "bool" else
                                  max(abs(x[1]) for x in d["score"][1]) if d["score"][0] == "num" else
                                  max(abs(v) for v in list(d["score"][1].values()) + [d["score"][2] if len(d["score"]) > 2 else 0])
                                  ) for d in dims_in_layer) or 1
            net_layer = sum(impacts)
            # 归一化到 0-100, 50为中性
            layer_score = max(0, min(100, round(50 + (net_layer / max(max_possible * 0.5, 1)) * 50)))
        else:
            layer_score = 50
            net_layer = 0

        top_in_layer = max(
            [f for f in all_factors if f["layer"] == lid],
            key=lambda x: abs(x["impact_pct"]),
            default=None
        )
        layer_results.append({
            "id": lid,
            "name": layer["name"],
            "weight": layer["weight"],
            "score": layer_score,
            "net_impact_pct": round(net_layer, 2),
            "factors_count": len(dims_in_layer),
            "top_factor": {"name": top_in_layer["name"], "impact_pct": top_in_layer["impact_pct"]} if top_in_layer else None,
        })

    # 综合评分 (加权)
    overall_score = round(sum(lr["score"] * lr["weight"] for lr in layer_results))
    overall_score = max(0, min(100, overall_score))

    # 评分等级
    if overall_score >= 80:
        score_level = "优秀"
    elif overall_score >= 65:
        score_level = "优良"
    elif overall_score >= 50:
        score_level = "中等"
    else:
        score_level = "较差"

    # 市场基线与估值
    baseline = _compute_market_baseline(listing, all_listings or [])
    estimated_value = round(baseline * (1 + net_impact_pct / 100.0), 1)
    price_gap_pct = round((listing_price - estimated_value) / estimated_value * 100, 1) if estimated_value > 0 else 0

    # 估值结论
    if abs(price_gap_pct) <= 5:
        verdict = "定价合理"
        verdict_detail = f"挂牌价与全息估值基本一致，定价合理。"
    elif price_gap_pct > 5:
        verdict = f"高于估值{price_gap_pct:.1f}%"
        verdict_detail = f"挂牌价{listing_price}万高于全息估值{estimated_value}万约{price_gap_pct:.1f}%，存在议价空间。"
    else:
        verdict = f"低于估值{abs(price_gap_pct):.1f}%"
        verdict_detail = f"挂牌价{listing_price}万低于全息估值{estimated_value}万约{abs(price_gap_pct):.1f}%，具备性价比。"

    # Top因素排序
    sorted_positive = sorted([f for f in all_factors if f["impact_pct"] > 0], key=lambda x: -x["impact_pct"])
    sorted_negative = sorted([f for f in all_factors if f["impact_pct"] < 0], key=lambda x: x["impact_pct"])

    return {
        "overall_score": overall_score,
        "score_level": score_level,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "estimated_value_wan": estimated_value,
        "listing_price_wan": listing_price,
        "market_baseline_wan": baseline,
        "price_gap_pct": price_gap_pct,
        "net_impact_pct": round(net_impact_pct, 2),
        "version": version,
        "dimensions_count": len(active_dims),
        "layers": layer_results,
        "top_positive": sorted_positive[:6],
        "top_negative": sorted_negative[:6],
        "all_factors": sorted(all_factors, key=lambda x: -abs(x["impact_pct"])),
    }


# === 用户录入房源评估 ===
# 表单字段 → 维度值 的直接映射
_FORM_TO_DIM = {
    # 基本信息 → 生成伪tags/desc
    "subway_distance": lambda v: {"l2_subway_dist": True if v and float(v) <= 1000 else False,
                                   "l2_desc_subway": True if v and float(v) <= 500 else False},
    "cbd_commute": lambda v: {"l2_cbd_commute": float(v) if v else 40},
    "school_level": lambda v: {"l2_school": v in ("市重点", "区重点"),
                               "l2_desc_school": v == "市重点",
                               "l2_school_policy": "单校划片" if v in ("市重点", "区重点") else "多校划片"},
    "hospital_min": lambda v: {"l2_hospital": float(v) if v else 20},
    "park_distance": lambda v: {"l2_park": True if v and float(v) <= 1000 else False},
    "mall_distance": lambda v: {"l2_mall": float(v) if v else 1500},
    "noise_level": lambda v: {"l2_noise": v == "安静", "l3_road_noise": "是" if v in ("临街", "临高架") else "否"},
    "view_type": lambda v: {"l4_view_type": v in ("湖景", "公园景", "无遮挡"),
                            "l2_water_view": v == "湖景",
                            "l4_view_block": "无遮挡" if v in ("湖景", "公园景", "无遮挡") else ("轻微" if v == "城市景" else "严重")},
    # 房屋属性
    "floor": lambda v: {"l4_floor_pos": float(v) if v else 10},
    "total_floors": lambda v: {"l4_top_floor": "是" if v else "否"},
    "orientation": lambda v: {"l4_living_orient": v or "南", "l4_master_orient": v or "南"},
    "north_south": lambda v: {"l4_north_south": bool(v),
                              "l4_living_orient": "南" if v else None,
                              "l4_master_orient": "南" if v else None},
    "decoration": lambda v: {"l4_deco_level": v in ("精装", "豪装"),
                             "l4_deco_luxury": v == "豪装",
                             "l4_deco_age": 1 if v in ("精装", "豪装") else (5 if v == "简装" else 15),
                             "l4_move_in": v in ("精装", "豪装")},
    "build_year": lambda v: {"l3_build_age": max(0, 2026 - int(v)) if v else 15,
                             "l3_build_year": (2026 - int(v)) <= 5 if v else False},
    "has_elevator": lambda v: {"l4_elevator": bool(v)},
    "elevator_ratio": lambda v: {"l4_elevator_ratio": v or "2T4"},
    "layout_rooms": lambda v: {},
    "layout_halls": lambda v: {"l4_dining": int(v) >= 1 if v else False},
    "layout_baths": lambda v: {"l4_master_bath": int(v) >= 2 if v else False},
    "area_size": lambda v: {},
    # 小区信息
    "far": lambda v: {"l3_far": float(v) if v else 2.5},
    "green_rate": lambda v: {"l3_green": float(v) if v else 35},
    "property_fee": lambda v: {"l3_property_fee": float(v) if v else 3.0},
    "property_brand": lambda v: {"l3_property_brand": v in ("万科", "龙湖", "绿城", "中海", "保利"),
                                  "l3_brand_dev": v in ("万科", "龙湖", "绿城", "中海", "保利")},
    "ped_car_split": lambda v: {"l3_ped_car": bool(v)},
    "parking_ratio": lambda v: {"l3_parking": float(v) if v else 0.8},
    "total_units": lambda v: {"l3_units": float(v) if v else 1000},
    "building_gap": lambda v: {"l3_building_gap": float(v) if v else 40},
    # 特殊情况
    "property_type": lambda v: {"l6_property_type": v == "商品房",
                                "l3_pure_residential": v != "商住两用"},
    "five_year_only": lambda v: {"l6_tax_5y": bool(v), "l6_tax_2y": "满五" if v else "满二不满五"},
    "has_mortgage": lambda v: {"l6_mortgage": "有" if v else "无"},
    "has_lease": lambda v: {"l6_lease": bool(v)},
    "is_haunted": lambda v: {"l5_haunted_self": "是" if v else "否"},
    "taboo_facilities": lambda v: {"l2_taboo_1km": v if v else "无",
                                    "l5_funeral_near": "近" if v in ("殃仪馆", "墓地") else "远"},
    "has_leak": lambda v: {"l4_leak": "严重" if v else "无"},
    "feng_shui_issue": lambda v: {"l5_road_rush": "有" if v == "路冲" else "无",
                                   "l5_bow_road": "有" if v == "反弓路" else "无",
                                   "l5_sky_cut": "有" if v == "天斩煞" else "无"},
    "urgent_sell": lambda v: {"l6_urgent_sell": bool(v)},
    "listing_days": lambda v: {"l6_listing_days": float(v) if v else 60},
    "ceiling_height": lambda v: {"l4_ceiling": float(v) if v else 2.7},
    "heating_type": lambda v: {"l4_heating": v or "集中供暖"},
    "has_central_ac": lambda v: {"l4_central_ac": "有" if v else "无"},
    "has_fresh_air": lambda v: {"l4_fresh_air": "有" if v else "无"},
    "window_type": lambda v: {"l4_window_type": v or "断桥铝", "l4_glass": "双层中空" if v == "断桥铝" else "单层"},
    "squareness": lambda v: {"l4_squareness": bool(v)},
    "dry_wet_sep": lambda v: {"l4_dry_wet": "有" if v else "无"},
    "bright_bath": lambda v: {"l4_bright_bath": "有窗" if v else "无窗"},
    "door_toilet": lambda v: {"l5_door_toilet": "是" if v else "否"},
    "beam_press": lambda v: {"l5_beam": "有" if v else "无"},
}


def evaluate_user_input(form_data: dict, version: str = "professional") -> dict:
    """
    用户录入房源信息的全息估值。
    form_data: 表单字段字典
    返回: 与 evaluate_listing 相同结构的结果 + 免费/付费分层
    """
    ver_level = VERSIONS.get(version, 3)
    active_dims = [d for d in DIMENSIONS if d["ver"] <= ver_level]

    # 第1步：从表单数据直接映射维度值
    direct_values = {}
    for field_name, mapper in _FORM_TO_DIM.items():
        val = form_data.get(field_name)
        if val is not None and val != "":
            try:
                mapped = mapper(val)
                for k, v in mapped.items():
                    if v is not None:
                        direct_values[k] = v
            except (ValueError, TypeError):
                pass

    # 第2步：构建伪 listing 用于未覆盖维度的模拟
    pseudo_id = form_data.get("community", "") + form_data.get("area_size", "") + form_data.get("address", "")
    pseudo_listing = {
        "id": hashlib.md5(pseudo_id.encode()).hexdigest()[:8].upper(),
        "community": form_data.get("community", "未知小区"),
        "address": form_data.get("address", ""),
        "layout": f"{form_data.get('layout_rooms', 2)}室{form_data.get('layout_halls', 1)}厅{form_data.get('layout_baths', 1)}卫",
        "area": float(form_data.get("area_size", 90)),
        "price": float(form_data.get("price", 300)),
        "tags": [],
        "description": "",
    }
    # 从表单生成tags
    tag_map = {
        "north_south": "南北通透", "has_elevator": "电梯房", "ped_car_split": "人车分流",
        "urgent_sell": "降价房", "five_year_only": "满五唯一", "decoration": None,
    }
    for field, tag in tag_map.items():
        if tag and form_data.get(field):
            pseudo_listing["tags"].append(tag)
    if form_data.get("decoration") in ("精装", "豪装"):
        pseudo_listing["tags"].append("精装修")
    if form_data.get("decoration") == "豪装":
        pseudo_listing["tags"].append("豪华装修")
    if form_data.get("view_type") == "湖景":
        pseudo_listing["tags"].append("湖景房")
    if form_data.get("school_level") in ("市重点", "区重点"):
        pseudo_listing["tags"].append("学区房")

    # 第3步：模拟未覆盖的维度
    sim_values = derive_listing_dimensions(pseudo_listing)
    # 用户直接提供的值覆盖模拟值
    sim_values.update(direct_values)

    # 第4步：评分
    all_factors = []
    layer_impacts = {layer["id"]: [] for layer in LAYERS}
    net_impact_pct = 0.0

    for dim in active_dims:
        did = dim["id"]
        raw_val = sim_values.get(did)
        impact, display = _score_dimension(dim, raw_val)
        net_impact_pct += impact
        layer_impacts[dim["layer"]].append(impact)
        all_factors.append({
            "id": did,
            "name": dim["name"],
            "layer": dim["layer"],
            "layer_name": next((l["name"] for l in LAYERS if l["id"] == dim["layer"]), ""),
            "category": dim["cat"],
            "impact_pct": round(impact, 2),
            "display_value": display,
        })

    listing_price = pseudo_listing["price"]
    for f in all_factors:
        f["impact_wan"] = round(listing_price * f["impact_pct"] / 100.0, 1)

    # 层级评分
    layer_results = []
    for layer in LAYERS:
        lid = layer["id"]
        impacts = layer_impacts[lid]
        dims_in_layer = [d for d in active_dims if d["layer"] == lid]
        if impacts:
            max_possible = sum(abs(d["score"][1] if d["score"][0] == "bool" else
                                  max(abs(x[1]) for x in d["score"][1]) if d["score"][0] == "num" else
                                  max(abs(v) for v in list(d["score"][1].values()) + [d["score"][2] if len(d["score"]) > 2 else 0])
                                  ) for d in dims_in_layer) or 1
            net_layer = sum(impacts)
            layer_score = max(0, min(100, round(50 + (net_layer / max(max_possible * 0.5, 1)) * 50)))
        else:
            layer_score = 50
            net_layer = 0
        top_in_layer = max(
            [f for f in all_factors if f["layer"] == lid],
            key=lambda x: abs(x["impact_pct"]), default=None
        )
        layer_results.append({
            "id": lid, "name": layer["name"], "weight": layer["weight"],
            "score": layer_score, "net_impact_pct": round(net_layer, 2),
            "factors_count": len(dims_in_layer),
            "top_factor": {"name": top_in_layer["name"], "impact_pct": top_in_layer["impact_pct"]} if top_in_layer else None,
        })

    overall_score = max(0, min(100, round(sum(lr["score"] * lr["weight"] for lr in layer_results))))
    if overall_score >= 80:
        score_level = "优秀"
    elif overall_score >= 65:
        score_level = "优良"
    elif overall_score >= 50:
        score_level = "中等"
    else:
        score_level = "较差"

    # 估值：基于用户报价 + 维度调整
    # 单价基线 = 用户报价/面积，估值 = 基线 × (1 + net_impact%)
    area = pseudo_listing["area"]
    unit_price = listing_price / area if area > 0 else 0
    estimated_total = round(listing_price * (1 + net_impact_pct / 100.0), 1)
    price_gap_pct = round(-net_impact_pct, 1)  # 正=用户报价偏高，负=偏低

    if abs(price_gap_pct) <= 5:
        verdict = "定价合理"
        verdict_detail = f"您的报价与全息估值基本一致，定价合理。"
    elif price_gap_pct > 5:
        verdict = f"报价偏高{price_gap_pct:.1f}%"
        verdict_detail = f"您的报价{listing_price}万高于全息估值{estimated_total}万，建议调整或等待合适买家。"
    else:
        verdict = f"报价偏低{abs(price_gap_pct):.1f}%"
        verdict_detail = f"您的报价{listing_price}万低于全息估值{estimated_total}万，具备很强竞争力。"

    sorted_positive = sorted([f for f in all_factors if f["impact_pct"] > 0], key=lambda x: -x["impact_pct"])
    sorted_negative = sorted([f for f in all_factors if f["impact_pct"] < 0], key=lambda x: x["impact_pct"])

    # 用户填写的字段数 vs 总维度数 → 数据完整度
    filled_fields = sum(1 for k, v in form_data.items() if v is not None and v != "")
    total_form_fields = len(_FORM_TO_DIM)
    data_completeness = round(filled_fields / max(total_form_fields, 1) * 100)

    return {
        "overall_score": overall_score,
        "score_level": score_level,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "estimated_value_wan": estimated_total,
        "listing_price_wan": listing_price,
        "unit_price_wan": round(unit_price, 2),
        "price_gap_pct": price_gap_pct,
        "net_impact_pct": round(net_impact_pct, 2),
        "version": version,
        "dimensions_count": len(active_dims),
        "data_completeness": data_completeness,
        "layers": layer_results,
        "top_positive": sorted_positive[:8],
        "top_negative": sorted_negative[:8],
        "all_factors": sorted(all_factors, key=lambda x: -abs(x["impact_pct"])),
        "form_data": form_data,
        # 免费预览 vs 付费完整报告
        "free_preview": {
            "overall_score": overall_score,
            "score_level": score_level,
            "verdict": verdict,
            "estimated_value_wan": estimated_total,
            "top_positive": sorted_positive[:3],
            "top_negative": sorted_negative[:3],
            "layers": layer_results,
        },
    }
