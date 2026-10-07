from app import app

# 预热：openai 包首次 import 有 1~2 秒开销，挪到进程启动阶段，
# 避免第一个访客请求 /valuate/ai_status 时被这段延迟拖到超时
try:
    import listing_parser  # noqa: F401

    listing_parser.vision_available()
except Exception:  # noqa: BLE001
    pass

application = app
