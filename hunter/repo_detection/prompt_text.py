"""
搜索侧深挖循环里注入给模型的固定文案，中英各一套，按 output_language 挑。

explorer 的逼停/半程/停滞提醒、JSON 修复重试、工具预算占位、清理占位符，audit 的锚点重修指令，
debate 和 query_understanding 的 JSON 修复消息，还有拼进 system 的空值占位，全在这里集中放。
这些只发给模型、用户看不到，但英文搜索时插一段中文会把模型带偏，所以跟着搜索语言走。取文案统一
走 texts(output_language)，返回一个 dict，调用方按键取。
"""

_ZH = {
    "force_stop": "系统提示:工具预算已用完，不要再调用任何工具，立即用现在掌握的信息，按规定格式输出最终 JSON。",
    "midpoint": ("系统提示:工具预算已用一半。对照输出契约盘点一下:purpose、tech_stack、"
                 "key_designs、architecture 各自有着落了吗?只查还缺的,都齐了就直接收尾输出 JSON。"),
    "stall": ("系统提示:你这一轮的工具调用没有获得任何新信息,说明该看的已经看得差不多了。"
              "不要再重复查,对照输出契约直接收尾输出 JSON。"),
    "json_fix": "输出的 JSON 格式不合法。内容不变，只修复格式，重新输出合法 JSON。",
    "json_rescue": ("还是不合法。别管之前的话，直接从你上面写过的内容里，"
                    "按 dissection 的输出契约提取信息，只输出一个合法 JSON，不要任何别的字。"),
    "repair_budget_out": "修复额度已用完，请用现有信息重新输出完整 JSON",
    "tool_budget_out": "工具预算已用完，请用现有信息收尾",
    "cleared": "(此处原为 {path} 行 {start}-{end} 的内容，已清理以节省上下文，如需可重新读取，至多两次)",
    "repair_head": "系统提示:你刚才输出的 key_designs 里，下面这些 where 引用在仓库里核对不上：\n",
    "repair_tail": ("请只针对这几条重新用工具核对源码：用 glob_files / grep_code / read_file 找到这个"
                    "设计点真正对应的文件和符号，把 where 改成查得到的正确锚点。如果某条设计点本身在"
                    "源码里找不到依据、站不住，就把它从 key_designs 里删掉，不要硬留。其余 key_designs "
                    "和别的字段保持原样。改完重新输出完整 JSON。"),
    "repair_line": '- "{name}" 的 where "{anchor}"：{reason}',
    "qu_bullets": "用户的需求清单：\n{bullets}",
    "qu_json_fix": "输出的 JSON 格式不合法，错误：{err}。内容不变，只修复格式，重新输出合法 JSON。",
    "none": "（无）",
    "no_tree": "（拿不到目录结构）",
    # 四个工具的返回文案。{已读过}{无命中}{没有匹配的文件}{文件不存在}{路径越界}{工具预算已用完}
    # 这几句同时是 explorer 停滞检测 FUTILE_MARKS 的匹配标记，改动时两处要一起对齐
    "tool_oob_view": "路径越界，只能在仓库内查看。",
    "tool_oob_read": "路径越界，只能在仓库内读取。",
    "tool_oob_search": "路径越界，只能在仓库内搜索。",
    "tool_oob_glob": "路径越界，只能在仓库内查找。",
    "tool_not_dir": "不是可列的目录：{path}，可能不存在或指向文件，文件请用 read_file 读。",
    "tool_root": "(根目录)",
    "tool_tree_capped": "\n...（共 {total} 条，只列前 {cap} 条）",
    "tool_empty_dir": "（空目录）",
    "tool_already_read": "该文件该段（{path} 行 {start}-{end}）已读过，内容未变，略。",
    "tool_reread_max": "该文件该段（{path} 行 {start}-{end}）已归档且重读达上限，不再重复读取，请用现有信息收尾。",
    "tool_not_file": "文件不存在: {path}",
    "tool_read_fail": "读取失败: {err}",
    "tool_read_capped": "\n...（内容过长已截断，用 offset/limit 读后续）",
    "tool_no_hit": "无命中。",
    "tool_search_err": "搜索出错：{err}",
    "tool_grep_capped": "\n...（共 {total} 行命中，只显示前 {cap} 行）",
    "tool_out_capped": "\n...（输出过长已截断）",
    "tool_no_file": "没有匹配的文件。",
    "tool_glob_err": "查找出错：{err}",
    "tool_glob_capped": "\n...（共 {total} 个，只显示前 {cap} 个）",
    "tool_bad_json": "参数不是合法 JSON：{args}",
    "tool_unknown": "未知工具 {name}，只能用 list_tree / read_file / grep_code / glob_files。",
    "tool_run_err": "工具执行出错：{err}",
    "tool_readme_given": "README 已在任务开头给你了，看那份，不用再读。",
    "tool_root_given": "根目录结构树已在任务开头给你了，直接看那棵树，要更深就 list_tree 具体子目录。",
    "anchor_path_missing": "路径不存在",
    "anchor_symbol_missing": "文件在，但没搜到符号 {symbol}",
    "note_none": "（还没有笔记）",
    "manifest_none": "（暂无已有记忆）",
}

_EN = {
    "force_stop": ("System note: the tool budget is used up. Don't call any more tools; use what you "
                   "have now and output the final JSON in the required format immediately."),
    "midpoint": ("System note: half the tool budget is used. Check against the output contract: are "
                 "purpose, tech_stack, key_designs and architecture each covered? Only look up what's "
                 "still missing; if all are covered, wrap up and output the JSON."),
    "stall": ("System note: this round's tool calls returned no new information, meaning you've "
              "seen about all there is. Don't repeat lookups; wrap up against the output contract "
              "and output the JSON."),
    "json_fix": "The JSON output is not valid. Keep the content unchanged, only fix the format, and output valid JSON again.",
    "json_rescue": ("Still not valid. Ignore the earlier wording; straight from what you wrote above, "
                    "extract the information per the dissection output contract and output a single "
                    "valid JSON, nothing else."),
    "repair_budget_out": "Repair budget is used up; use the current information and output the complete JSON again.",
    "tool_budget_out": "Tool budget is used up; wrap up with the current information.",
    "cleared": "(This was the content of {path} lines {start}-{end}; cleared to save context, re-readable if needed, at most twice)",
    "repair_head": "System note: in the key_designs you just output, these where references don't check out against the repo:\n",
    "repair_tail": ("Re-verify only these against the source with the tools: use glob_files / grep_code / "
                    "read_file to find the file and symbol this design point actually corresponds to, and "
                    "change where to a resolvable anchor. If a design point has no basis in the source and "
                    "doesn't hold up, drop it from key_designs rather than forcing it in. Keep the other "
                    "key_designs and fields as-is. Output the complete JSON again when done."),
    "repair_line": '- "{name}" where "{anchor}": {reason}',
    "qu_bullets": "The user's requirements:\n{bullets}",
    "qu_json_fix": "The JSON output is not valid, error: {err}. Keep the content unchanged, only fix the format, and output valid JSON again.",
    "none": "(none)",
    "no_tree": "(directory structure unavailable)",
    "tool_oob_view": "Path out of bounds; you can only view inside the repo.",
    "tool_oob_read": "Path out of bounds; you can only read inside the repo.",
    "tool_oob_search": "Path out of bounds; you can only search inside the repo.",
    "tool_oob_glob": "Path out of bounds; you can only look up inside the repo.",
    "tool_not_dir": "Not a listable directory: {path}; it may not exist or points to a file. Use read_file for files.",
    "tool_root": "(root)",
    "tool_tree_capped": "\n...({total} entries total, showing first {cap})",
    "tool_empty_dir": "(empty directory)",
    "tool_already_read": "This file segment ({path} lines {start}-{end}) was already read, content unchanged, skipped.",
    "tool_reread_max": "This file segment ({path} lines {start}-{end}) is archived and has hit the re-read limit; not reading again, wrap up with the current information.",
    "tool_not_file": "File does not exist: {path}",
    "tool_read_fail": "Read failed: {err}",
    "tool_read_capped": "\n...(content too long, truncated; use offset/limit to read the rest)",
    "tool_no_hit": "No matches.",
    "tool_search_err": "Search error: {err}",
    "tool_grep_capped": "\n...({total} matching lines total, showing first {cap})",
    "tool_out_capped": "\n...(output too long, truncated)",
    "tool_no_file": "No matching files.",
    "tool_glob_err": "Lookup error: {err}",
    "tool_glob_capped": "\n...({total} total, showing first {cap})",
    "tool_bad_json": "Arguments are not valid JSON: {args}",
    "tool_unknown": "Unknown tool {name}; only list_tree / read_file / grep_code / glob_files are available.",
    "tool_run_err": "Tool execution error: {err}",
    "tool_readme_given": "The README was given at the start of the task; use that one, no need to read it again.",
    "tool_root_given": "The root directory tree was given at the start of the task; look at that tree, and list_tree a specific subdirectory to go deeper.",
    "anchor_path_missing": "path does not exist",
    "anchor_symbol_missing": "file exists but symbol {symbol} not found",
    "note_none": "(no note yet)",
    "manifest_none": "(no existing memories)",
}


def texts(output_language: str = "简体中文") -> dict:
    """按 output_language 挑文案包，English 用英文，其余一律中文。"""
    return _EN if output_language == "English" else _ZH
