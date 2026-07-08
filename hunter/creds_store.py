"""凭证读写到 data/creds.json，前端进入时存一份、下次自动带出来。纯数据层，webapp 只管调。"""

import json
from pathlib import Path

# 存在项目根的 data/ 下，跟历史库同一个目录，data/ 已进 gitignore 不会带进 git
CREDS_PATH = Path(__file__).resolve().parent.parent / "data" / "creds.json"


def load_creds() -> dict:
    """读已存的凭证（含模型），没存过或读坏了都返回空串，前端拿空串就当没存。"""
    blank = {"deepseek_api_key": "", "github_pat": "", "model": "", "models": {}}
    if not CREDS_PATH.exists():
        return blank
    try:
        d = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
        return {"deepseek_api_key": d.get("deepseek_api_key", ""),
                "github_pat": d.get("github_pat", ""),
                "model": d.get("model", ""),
                "models": d.get("models", {})}
    except Exception:
        return blank


def save_creds(deepseek_api_key: str, github_pat: str, model: str = "",
               models: dict | None = None) -> None:
    """把这次填的凭证和模型写进本地文件覆盖旧的，下次自动带出来。全空就不写。"""
    if not deepseek_api_key and not github_pat and not model and not models:
        return
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(
        json.dumps({"deepseek_api_key": deepseek_api_key, "github_pat": github_pat,
                    "model": model, "models": models or {}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
