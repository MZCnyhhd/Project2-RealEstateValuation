# RealEstate Demo (Flask)

一个用于演示的房源检索网站（Flask + Bootstrap），支持：

- 列表筛选（价格/面积）
- 关键词搜索（title/community/address/description）
- 标签多选过滤
- 排序（价格/面积升降序）
- 分页（带省略号窗口）
- 收藏（Session）与对比（最多 5 条）
- JSON / SQLite 数据后端可切换

## 在线访问（Render）

> 部署成功后，你会得到一个公网 URL，可直接放到简历中。

### 1) 推送到 GitHub

把本项目推到你的 GitHub 仓库。

### 2) Render 创建 Web Service

在 Render 控制台选择：

- New -> Web Service
- 选择你的 GitHub repo

配置如下：

- Build Command

```bash
pip install -r requirements.txt
```

- Start Command

```bash
gunicorn wsgi:application --bind 0.0.0.0:$PORT
```

### 3) Render 环境变量（建议设置）

- `SECRET_KEY`
  - 任意长随机字符串
  - 用于 Session（收藏/对比）

可选：

- `DATA_BACKEND`
  - `json`（默认）
  - `sqlite`

当 `DATA_BACKEND=sqlite` 时可选：

- `SQLITE_PATH`
  - 默认 `realestate.db`
- `SQLITE_AUTO_IMPORT`
  - `1`：启动时自动从 `mock_listings.json` 导入到 SQLite
  - `0`：不自动导入

## 本地运行（Windows / Conda）

1) 创建并激活环境（示例）

```bash
conda create -n PythonProject-RealEstate python=3.12
conda activate PythonProject-RealEstate
```

2) 安装依赖

```bash
pip install -r requirements.txt
```

3) 启动

```bash
python app.py
```

打开：

- http://127.0.0.1:5000/

## 数据扩容（可选）

如果你想生成更多房源：

```bash
python update_images.py
```

注意：如果要从 Unsplash 拉取新图片，需要可用的 Access Key（当前仓库内 Key 可能失效/403）。

## 主要路由

- `/`：列表（筛选/搜索/排序/分页）
- `/listing/<id>`：详情
- `/favorites`：收藏列表
- `/compare`：对比页面
