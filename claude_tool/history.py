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


def title_of(path):
    """一条会话给人看的标题。一条都取不到就退回空串。"""
    try:
        tail = _piece(path, True, TAIL_BYTES)
        title = _title_in(tail, last=True)
        if title:
            return title
        head = _piece(path, False, HEAD_BYTES)
        title = _title_in(head, last=False) or _first_prompt(head)
        if title:
            return title
        prompt = _last_prompt(tail)
        if prompt:
            return prompt
    except OSError:
        pass
    return ""


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

    每项：id（uuid）、title、updated（mtime 时间戳）、file（绝对路径）。
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
        sessions.append({
            "id": session_id,
            "title": title_of(file) or "会话 " + session_id[:8],
            "updated": stamp,
            "file": file,
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
        payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            # 先单独看一眼上一行有没有换行收尾。写入用 "ab"（追加模式）而不是
            # "r+b" + seek：会话正开着时 CLI 也在往同一个文件追加，追加模式由
            # 系统保证每次都落在当前末尾，不会把 CLI 刚写的那截盖掉。
            with open(session["file"], "rb") as handle:
                handle.seek(0, os.SEEK_END)
                needs_newline = bool(handle.tell())
                if needs_newline:
                    handle.seek(-1, os.SEEK_END)
                    needs_newline = handle.read(1) != b"\n"
            with open(session["file"], "ab") as handle:
                if needs_newline:
                    handle.write(b"\n")
                handle.write(payload)
        except OSError:
            return False
        return True
    return False


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
