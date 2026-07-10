"""临时测试启动脚本，不属于项目代码，测完即删。内存里把压缩常量改小，不动任何硬盘项目文件。"""
import uvicorn
import hunter.memory.compact.compact as compact_mod
import hunter.memory.compact.note as note_mod
import hunter.chat.session as session_mod
import hunter.repo_detection.explorer as explorer_mod

compact_mod.COMPACT_THRESHOLD = 5000
compact_mod.KEEP_MIN_TOKENS = 500
compact_mod.KEEP_MAX_TOKENS = 2000
note_mod.NOTE_TOKEN_THRESHOLD = 200
session_mod.COMPACT_THRESHOLD = 5000
explorer_mod.CLEAN_TRIGGER_TOKENS = 2000
explorer_mod.CLEAN_TARGET_TOKENS = 1200
explorer_mod.CLEAN_KEEP_START = 2
print("=== 测试参数已生效 threshold=5000 ===", flush=True)

from webapp.backend.server import app  # noqa: E402
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8755)
