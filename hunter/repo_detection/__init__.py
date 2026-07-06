"""repo_detection 子包：六角色深挖引擎。

content_filter 是本阶段总编排（Repo_Detection），explorer 跑 gate/explorer 工具循环出拆解，
debate 做正反辩论加裁决，audit 核锚点，tool_loop 和 log_visual 是共用底件，agent_tools 提供
四个只读工具，prefetch 预抓 README 和目录树，scoring 管对齐算分排序。编排入口和 _fill_*
从这里 re-export，外部按 hunter.repo_detection 直接取。
"""
from hunter.repo_detection.content_filter import (
    Repo_Detection, _fill_system, _fill_gate, _fill_explore,
)

__all__ = ["Repo_Detection", "_fill_system", "_fill_gate", "_fill_explore"]
