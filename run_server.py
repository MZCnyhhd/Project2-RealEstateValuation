"""本地开发服务器启动脚本：环境变量在此设置，供本地预览使用。"""
import os

os.environ.setdefault("DATA_BACKEND", "json")
os.environ.setdefault("ADMIN_PASSWORD", "local_dev_pass")

# 加载本地密钥（local_secrets.py 已 gitignore，勿提交仓库）
try:
    import local_secrets  # noqa: F401

    for _k in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "BJ_DATA_USERKEY"):
        _v = getattr(local_secrets, _k, None)
        if _v:
            os.environ.setdefault(_k, str(_v))
except ImportError:
    pass

from app import app  # noqa: E402

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
