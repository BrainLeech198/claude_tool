"""读 CLI 落盘的会话记录：这一本工作区聊过哪几条、各叫什么、多久以前。

**会话文件是唯一真源**，不给启动器另存一份索引：用户在启动器外面开的会话也得
能看见，自己维护一份迟早对不上。

两个 CLI 的目录规则不一样，不能只算一个路径：

  - claude：工作目录里所有非字母数字的字符换成 `-`（`D:\\Desktop` → `D--Desktop`）
  - codebuddy：`compressPath`，斜杠/反斜杠/冒号换成 `-` 再去首尾、合并连续短横
    （`D:\\Desktop` → `d-Desktop`）。它用的是 realpath 之后的 cwd，而盘符大小写
    取决于启动时的写法——现有记录里是小写 `d-`。所以候选里两种都试。

单条会话的文件名是会话 id（uuid）。标题优先取 `ai-title` 行（CLI 自己生成的），
没有再退回第一条用户消息；时间用文件 mtime——每次对话都会往里追加。
"""
import json
import os
import re
import shutil

from claude_tool import agents

# 只读文件的一头一尾：一份聊了很久的会话能到几 MB，为了列个标题整份读进来不划算。
# 标题行是随对话追加的，尾部那份最新；头部留给"还没有 ai-title 的新会话"。
TAIL_BYTES = 48 * 1024
HEAD_BYTES = 24 * 1024
# 预览读最近这么多字节。一屏只摆几条消息，但单条可能很长（贴了大段日志），
# 留够 128KB 一般够翻出最近七八条；真不够就少给几条，不为了补满整份读进来。
PREVIEW_BYTES = 128 * 1024


def compress_path(path):
    """codebuddy 的 `PathUtils.compressPath`，照抄它的四步。"""
    text = re.sub(r"[/\\:]", "-", path)
    text = text.lstrip("-").rstrip("-")
    return re.sub(r"-+", "-", text)


def _claude_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _lower_drive(path):
    """把盘符改成小写；不是 `X:\\` 开头就原样返回。"""
    if len(path) > 1 and path[1] == ":" and path[0].isalpha():
        return path[0].lower() + path[1:]
    return path


def _variants(path):
    """同一个工作区可能有几种写法，去重后都要试。"""
    found = []
    for value in (path, os.path.realpath(path)):
        for item in (value, _lower_drive(value)):
            key = os.path.normcase(os.path.normpath(item))
            if key not in {os.path.normcase(os.path.normpath(x)) for x in found}:
                found.append(item)
    return found


def project_dirs(agent_id, workspace):
    """这个工作区在那一档的会话记录目录，返回实际存在的那些。

    按名字比对而不是直接拼路径：盘符大小写、realpath 展开与否都会让拼出来的
    名字对不上，而目录里的名字是既成事实。normcase 在 Windows 上顺带把大小写
    抹平了。
    """
    profile = agents.get(agent_id)
    slugber = compress_path if profile.family == "codebuddy" else _claude_slug
    wanted = {os.path.normcase(slugber(path)) for path in _variants(workspace)}
    try:
        names = os.listdir(profile.projects_dir)
    except OSError:
        return []
    return [os.path.join(profile.projects_dir, name) for name in names
            if os.path.normcase(name) in wanted]


def _piece(path, from_end, size):
    """读文件头或尾的一段，UTF-8 解不开的字节用替换字符顶上。"""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        total = handle.tell()
        start = max(total - size, 0) if from_end else 0
        handle.seek(start)
        data = handle.read(size)
    text = data.decode("utf-8", "replace")
    if start:
        # 第一行多半是从中间切开的，丢掉。
        cut = text.find("\n")
        text = text[cut + 1:] if cut >= 0 else ""
    return text


def _rows(text):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _text_of(content):
    """消息内容 -> 纯文本。字符串直接给，块列表只挑 text 块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "\n".join(parts)
    return ""


def _first_prompt(text):
    """第一条像人话的用户消息。系统提醒、命令包装、工具结果都跳过。"""
    for row in _rows(text):
        if row.get("type") != "user" or row.get("isMeta"):
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else message
        body = _text_of(content).strip()
        if not body or body.startswith("<"):
            # 尖括号开头的是 CLI 自己塞进来的（system-reminder、命令回显）。
            continue
        line = re.sub(r"\s+", " ", body)
        return line[:80]
    return ""


def _title_in(text, last):
    """这段文本里的 ai-title；last=True 取最后一条。"""
    found = ""
    for row in _rows(text):
        if row.get("type") == "ai-title" and row.get("aiTitle"):
            found = str(row["aiTitle"]).strip()
            if not last:
                break
    return found


def _last_prompt(text):
    found = ""
    for row in _rows(text):
        if row.get("type") == "last-prompt" and row.get("lastPrompt"):
            found = re.sub(r"\s+", " ", str(row["lastPrompt"]).strip())
    return found[:80]


def clean_tags(values):
    """标签列表去空、去重、去掉开头的 #。"""
    found = []
    for value in values or ():
        tag = " ".join(str(value).split()).lstrip("#")
        if tag and tag not in found:
            found.append(tag)
    return found


def _title_and_flags(path):
    """一次读头尾，把标题、收藏、标签都取出来。

    返回 (title, favorite, tags)。收藏和标签是启动器自己往文件末尾补的
    `session-flags` 行（见 set_flags），取最后一条——后写的盖前面的，跟
    ai-title 一个道理。
    """
    try:
        tail = _piece(path, True, TAIL_BYTES)
        head = _piece(path, False, HEAD_BYTES)
    except OSError:
        return "", False, []
    title = (_title_in(tail, last=True) or _title_in(head, last=False)
             or _first_prompt(head) or _last_prompt(tail))
    favorite, tags = False, []
    for row in _rows(tail):
        if row.get("type") == "session-flags":
            favorite = bool(row.get("favorite"))
            tags = clean_tags(row.get("tags"))
    return title, favorite, tags


def title_of(path):
    """一条会话给人看的标题。一条都取不到就退回空串。"""
    return _title_and_flags(path)[0]


_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _cwd_matches(path, wanted):
    """文件里记的 cwd 跟这个工作区对得上吗。

    只用来挡"slug 撞车"：`D:\\a-b` 和 `D:\\a\\b` 会压成同一个目录名。文件头
    还读不到 cwd（有些版本前几行没有）时就当它是对的。
    """
    try:
        head = _piece(path, False, HEAD_BYTES)
    except OSError:
        return False
    found = []
    for raw in _CWD_RE.findall(head):
        try:
            found.append(json.loads('"' + raw + '"'))
        except ValueError:
            continue
    if not found:
        return True
    keys = {os.path.normcase(os.path.normpath(item)) for item in wanted}
    return any(os.path.normcase(os.path.normpath(item)) in keys for item in found)


def list_sessions(agent_id, workspace, limit=None):
    """这一本工作区在一个档位下的会话，新的在前。

    每项：id（uuid）、title、updated（mtime 时间戳）、file（绝对路径）、
    favorite（收藏）、tags（标签列表）。
    """
    variants = _variants(workspace)
    files = []
    seen = set()
    for folder in project_dirs(agent_id, workspace):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            file = os.path.join(folder, name)
            key = os.path.normcase(os.path.realpath(file))
            if key in seen:
                continue
            seen.add(key)
            try:
                stamp = os.path.getmtime(file)
            except OSError:
                continue
            files.append((stamp, file))
    files.sort(key=lambda pair: pair[0], reverse=True)
    if limit:
        files = files[:limit]

    sessions = []
    for stamp, file in files:
        if not _cwd_matches(file, variants):
            continue
        session_id = os.path.basename(file)[:-len(".jsonl")]
        title, favorite, tags = _title_and_flags(file)
        sessions.append({
            "id": session_id,
            "title": title or "会话 " + session_id[:8],
            "updated": stamp,
            "file": file,
            "favorite": favorite,
            "tags": tags,
        })
    return sessions


def delete_session(agent_id, workspace, session_id):
    """删掉一条会话：那份 jsonl，外加同名的工具结果目录。

    CLI 会给部分会话在同目录建一个同名文件夹（tool-results 之类），只删 jsonl
    会留下它。`memory` 不是会话 id，这里也挡一道，免得手滑传进来把记忆删了。
    """
    if not session_id or session_id == "memory":
        return False
    for session in list_sessions(agent_id, workspace):
        if session["id"] != session_id:
            continue
        file = session["file"]
        try:
            os.remove(file)
        except OSError:
            return False
        shutil.rmtree(os.path.join(os.path.dirname(file), session_id),
                      ignore_errors=True)
        return True
    return False


def _append_row(path, row):
    """往会话文件末尾补一行。

    写入用 "ab"（追加模式）而不是 "r+b" + seek：会话正开着时 CLI 也在往同一个
    文件追加，追加模式由系统保证每次都落在当前末尾，不会把 CLI 刚写的那截盖掉。
    上一行没换行收尾时先补一个，免得两行粘成一行 JSON。
    """
    payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            needs_newline = bool(handle.tell())
            if needs_newline:
                handle.seek(-1, os.SEEK_END)
                needs_newline = handle.read(1) != b"\n"
        with open(path, "ab") as handle:
            if needs_newline:
                handle.write(b"\n")
            handle.write(payload)
    except OSError:
        return False
    return True


def rename_session(agent_id, workspace, session_id, title):
    """给一条会话改个名字：往那份 jsonl 末尾补一条 `ai-title`。

    CLI 自己的标题也是这种行，读的时候取最后一条（见 title_of），所以补在末尾
    就是「后写的盖前面的」，启动器和 CLI 都认。会话文件仍是唯一真源，不另存
    一份索引——索引迟早跟文件对不上。
    """
    title = " ".join(str(title).split())
    if not title or not session_id:
        return False
    for session in list_sessions(agent_id, workspace):
        if session["id"] != session_id:
            continue
        row = {"type": "ai-title", "aiTitle": title, "sessionId": session_id}
        return _append_row(session["file"], row)
    return False


def set_flags(agent_id, workspace, session_id, favorite, tags):
    """记下一条会话的收藏和标签：往 jsonl 末尾补一条 `session-flags`。

    每次写的是**完整快照**（不是增量），读的时候取最后一条（见
    `_title_and_flags`）。这样取消收藏、清空标签也只需要再补一行 false / []，
    不用回头改前面那些行——会话文件仍然只追加，不重写。

    返回清理后的标签列表（写成功）；找不到会话返回 None。
    """
    tags = clean_tags(tags)
    if not session_id:
        return None
    for session in list_sessions(agent_id, workspace):
        if session["id"] != session_id:
            continue
        row = {"type": "session-flags", "sessionId": session_id,
               "favorite": bool(favorite), "tags": tags}
        return tags if _append_row(session["file"], row) else None
    return None


def latest_time(agent_id, workspace):
    """这一本最近一次动过的时间；没聊过返回 None。"""
    try:
        stamps = []
        for folder in project_dirs(agent_id, workspace):
            for name in os.listdir(folder):
                if name.endswith(".jsonl"):
                    try:
                        stamps.append(os.path.getmtime(os.path.join(folder, name)))
                    except OSError:
                        continue
        return max(stamps) if stamps else None
    except OSError:
        return None


# ── 会话中心：跨工作区的那一份 ─────────────────────────────────────────────

def timeline(workspaces, per_workspace=200):
    """把所有工作区的会话聚成一条按时间倒序的时间线。

    `list_sessions` 只管一本；会话中心要的是"不管哪个项目，最近聊过什么"。
    每项在它返回的那几个字段之外多两样：`workspace`（配置里那条 dict）和
    `agent`（规范化之后的档位 id），右边预览和「接着聊」都要靠它们找回现场。

    per_workspace 默认 200（跟「查看全部」那个上限一致）：时间线是拿来翻最近
    的东西的，一个项目压着上千条旧会话时，为了列出来把整份历史都读一遍不值当。
    """
    found = []
    for entry in workspaces or ():
        path = entry.get("path")
        if not path or not os.path.isdir(path):
            continue
        agent_id = agents.get(entry.get("agent")).id
        try:
            sessions = list_sessions(agent_id, path, limit=per_workspace)
        except Exception:
            # 会话文件是外部数据，一本读坏了不该让整条时间线空掉。
            continue
        for session in sessions:
            item = dict(session)
            item["workspace"] = entry
            item["agent"] = agent_id
            found.append(item)
    found.sort(key=lambda item: item["updated"], reverse=True)
    return found


def _snippet(text, needle, radius=48):
    """关键词前后各留一小段，压成一行给搜索结果显示。"""
    index = text.lower().find(needle.lower())
    if index < 0:
        return ""
    start = max(index - radius, 0)
    end = min(index + len(needle) + radius, len(text))
    piece = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("…" if start else "") + piece + ("…" if end < len(text) else "")


def _simple_hit(path, needle):
    """简单模式：只看标题、开场白和最近一条提示。返回 (命中字段, 摘要)。"""
    try:
        tail = _piece(path, True, TAIL_BYTES)
        head = _piece(path, False, HEAD_BYTES)
    except OSError:
        return "", ""
    title = _title_in(tail, last=True) or _title_in(head, last=False)
    fields = (("标题", title),
              ("开场白", _first_prompt(head)),
              ("最近提示", _last_prompt(tail)))
    for where, text in fields:
        if text and needle in text.lower():
            return where, _snippet(text, needle)
    return "", ""


def _deep_hit(path, needle):
    """深度模式：整份扫一遍，正文、标题、最近提示都算。返回 (命中字段, 摘要)。

    只认消息里真正的 text 块（`_text_of`），工具结果、图片 base64 那些不进
    搜索范围——它们要么没人读得懂，要么会把关键词匹配成一堆垃圾。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("type")
                where = ""
                if kind == "ai-title":
                    text, where = str(row.get("aiTitle") or ""), "标题"
                elif kind in ("user", "assistant") and not row.get("isMeta"):
                    message = row.get("message")
                    content = (message.get("content")
                               if isinstance(message, dict) else message)
                    text, where = _text_of(content), "正文"
                elif kind == "last-prompt":
                    text, where = str(row.get("lastPrompt") or ""), "最近提示"
                else:
                    continue
                if text and needle in text.lower():
                    return where, _snippet(text, needle)
    except OSError:
        return "", ""
    return "", ""


def search(sessions, keyword, deep=False, progress=None):
    """在一批会话里按关键词筛，返回命中的那些（原样复制 + 两格命中信息）。

    `sessions` 是 `timeline()` 或 `list_sessions()` 的返回值；两种形状都够用
    ——只读 file，别的一概不碰。`progress(done, total)` 给深度模式报进度用：
    整份扫描可能要好几秒，界面得知道它还在动。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return list(sessions)
    needle = keyword.lower()
    found = []
    total = len(sessions)
    for index, session in enumerate(sessions):
        where, hit = (_deep_hit if deep else _simple_hit)(session["file"], needle)
        if hit:
            item = dict(session)
            item["match_where"] = where
            item["match_text"] = hit
            found.append(item)
        if progress is not None:
            progress(index + 1, total)
    return found


def preview(path, count=8):
    """一条会话最近几条人和 AI 说的话，给会话中心右边那块预览。

    返回 [{"role": "user"/"assistant", "text": ...}, ...]，按时间先后。读尾部
    就够了——预览要的是"聊到哪儿了"，不是从头复述。工具结果、系统提醒这类
    不是人话的行一律跳过。
    """
    try:
        tail = _piece(path, True, PREVIEW_BYTES)
    except OSError:
        return []
    messages = []
    for row in _rows(tail):
        kind = row.get("type")
        if kind not in ("user", "assistant") or row.get("isMeta"):
            continue
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else message
        text = _text_of(content).strip()
        if not text:
            continue
        if kind == "user" and text.startswith("<"):
            # CLI 自己塞进来的 system-reminder / 命令回显，不是用户说的话。
            continue
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        messages.append({"role": kind, "text": text})
    return messages[-count:]
