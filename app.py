import json
import os
from math import ceil
from flask import Flask, render_template, request, redirect, session

from repository import get_repository

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret")

def load_listings(filename="mock_listings.json"):
    repo = get_repository(filename=filename)
    return repo.list_listings()


def _favorites_set():
    fav = session.get("favorites", [])
    if not isinstance(fav, list):
        fav = []
    return set([x for x in fav if isinstance(x, str)])


def _compare_set():
    cmp_ = session.get("compare", [])
    if not isinstance(cmp_, list):
        cmp_ = []
    return set([x for x in cmp_ if isinstance(x, str)])


def _save_set(key, value_set, limit=None):
    ids = sorted(list(value_set))
    if limit is not None:
        ids = ids[:limit]
    session[key] = ids

def search_listings(listings, min_price=None, max_price=None, min_area=None, max_area=None, q=None, tags=None):
    """根据多个条件筛选房源"""
    results = listings

    if min_price is not None:
        results = [listing for listing in results if listing['price'] >= min_price]
    if max_price is not None:
        results = [listing for listing in results if listing['price'] <= max_price]
    if min_area is not None:
        results = [listing for listing in results if listing['area'] >= min_area]
    if max_area is not None:
        results = [listing for listing in results if listing['area'] <= max_area]

    if q:
        q_lower = q.strip().lower()
        if q_lower:
            def _match(listing):
                haystack = " ".join(
                    str(listing.get(k, ""))
                    for k in ("title", "community", "address", "description")
                ).lower()
                return q_lower in haystack

            results = [listing for listing in results if _match(listing)]

    if tags:
        tag_set = set([t for t in tags if t])
        if tag_set:
            results = [
                listing
                for listing in results
                if tag_set.issubset(set(listing.get("tags", [])))
            ]

    return results

@app.route("/")
def index():
    """主页和搜索结果页"""
    all_listings = load_listings()

    # 从URL参数获取搜索条件和排序方式
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)
    min_area = request.args.get('min_area', type=float)
    max_area = request.args.get('max_area', type=float)
    sort_by = request.args.get('sort_by', 'default')
    q = request.args.get('q', type=str, default='')
    selected_tags = request.args.getlist('tags')
    page = request.args.get('page', type=int, default=1)
    page_size = request.args.get('page_size', type=int, default=12)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 12
    if page_size > 48:
        page_size = 48

    # 筛选房源
    search_results = search_listings(all_listings, min_price, max_price, min_area, max_area, q=q, tags=selected_tags)

    # 排序结果
    if sort_by == 'price_asc':
        search_results.sort(key=lambda x: x['price'])
    elif sort_by == 'price_desc':
        search_results.sort(key=lambda x: x['price'], reverse=True)
    elif sort_by == 'area_asc':
        search_results.sort(key=lambda x: x['area'])
    elif sort_by == 'area_desc':
        search_results.sort(key=lambda x: x['area'], reverse=True)

    url_params = request.args.to_dict()
    url_params_multi = request.args.to_dict(flat=False)

    base_params = dict(url_params_multi)
    base_params.pop("sort_by", None)
    base_params.pop("page", None)
    sort_urls = {
        "price_asc": app.url_for("index", **base_params, sort_by="price_asc"),
        "price_desc": app.url_for("index", **base_params, sort_by="price_desc"),
        "area_asc": app.url_for("index", **base_params, sort_by="area_asc"),
        "area_desc": app.url_for("index", **base_params, sort_by="area_desc"),
    }

    total_results = len(search_results)
    total_pages = max(1, int(ceil(total_results / float(page_size))))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    end = start + page_size
    paged_listings = search_results[start:end]

    page_urls = {"prev": None, "next": None, "pages": []}

    page_base_params = dict(url_params_multi)
    page_base_params.pop("page", None)
    if page > 1:
        page_urls["prev"] = app.url_for("index", **page_base_params, page=page - 1)
    if page < total_pages:
        page_urls["next"] = app.url_for("index", **page_base_params, page=page + 1)

    window = 2
    candidates = {1, total_pages}
    for p in range(max(1, page - window), min(total_pages, page + window) + 1):
        candidates.add(p)
    ordered = sorted(candidates)
    last = None
    for p in ordered:
        if last is not None and p - last > 1:
            page_urls["pages"].append({"num": None, "url": None, "active": False, "label": "..."})
        page_urls["pages"].append(
            {
                "num": p,
                "url": app.url_for("index", **page_base_params, page=p),
                "active": p == page,
                "label": str(p),
            }
        )
        last = p

    available_tags = sorted({t for item in all_listings for t in item.get("tags", [])})
    favorites = _favorites_set()
    compare = _compare_set()

    return render_template(
        "index.html",
        listings=paged_listings,
        total_results=total_results,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        sort_by=sort_by,
        url_params=url_params,
        sort_urls=sort_urls,
        page_urls=page_urls,
        q=q,
        available_tags=available_tags,
        selected_tags=selected_tags,
        favorites=favorites,
        compare=compare,
    )


@app.route("/favorite/<listing_id>")
def toggle_favorite(listing_id):
    fav = _favorites_set()
    if listing_id in fav:
        fav.remove(listing_id)
    else:
        fav.add(listing_id)
    _save_set("favorites", fav)
    next_url = request.args.get("next") or app.url_for("index")
    return redirect(next_url)


@app.route("/compare/<listing_id>")
def toggle_compare(listing_id):
    cmp_ = _compare_set()
    if listing_id in cmp_:
        cmp_.remove(listing_id)
    else:
        cmp_.add(listing_id)
    _save_set("compare", cmp_, limit=5)
    next_url = request.args.get("next") or app.url_for("index")
    return redirect(next_url)


@app.route("/favorites")
def favorites_page():
    all_listings = load_listings()
    fav = _favorites_set()
    listings = [x for x in all_listings if x.get("id") in fav]
    return render_template("favorites.html", listings=listings, favorites=fav, compare=_compare_set())


@app.route("/compare")
def compare_page():
    all_listings = load_listings()
    cmp_ = _compare_set()
    listings = [x for x in all_listings if x.get("id") in cmp_]
    return render_template("compare.html", listings=listings, favorites=_favorites_set(), compare=cmp_)

@app.route("/listing/<listing_id>")
def listing_detail(listing_id):
    """房源详情页"""
    all_listings = load_listings()
    # 查找与传入ID匹配的房源
    listing = next((item for item in all_listings if item["id"] == listing_id), None)
    
    if listing:
        return render_template(
            "listing_detail.html",
            listing=listing,
            favorites=_favorites_set(),
            compare=_compare_set(),
        )
    else:
        return render_template("404.html"), 404

if __name__ == "__main__":
    app.run(debug=True)