"""本地开发服务器启动脚本：环境变量在此设置，供本地预览使用。"""
import os

os.environ.setdefault("DATA_BACKEND", "json")
os.environ.setdefault("ADMIN_PASSWORD", "local_dev_pass")

# 加载本地密钥（local_secrets.py 已 gitignore，勿提交仓库）
try:
    import local_secrets  # noqa: F401

    for _k in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "BJ_DATA_USERKEY",
               "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_VL_MODEL"):
        _v = getattr(local_secrets, _k, None)
        if _v:
            os.environ.setdefault(_k, str(_v))
except ImportError:
    pass

from app import app  # noqa: E402

# 预热：openai 包首次 import 有 1~2 秒开销，挪到启动阶段，
# 避免第一个访客请求 /valuate/ai_status 时被这段延迟拖到超时
try:
    import listing_parser  # noqa: F401

    listing_parser.vision_available()
except Exception:  # noqa: BLE001
    pass

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
