"""Portable, readable editing artifacts; no model or CLI integration required."""
import difflib
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

TEXT_TYPES = {"html", "htm", "css", "js", "json", "md", "svg", "txt"}
SCOPES = {"current": "仅当前元素／页面", "similar": "检查语义或结构相近的页面", "all": "检查全稿一致性"}


def fenced(text, language=""):
    fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", text)), default=3))
    return f"{fence}{language}\n{text}\n{fence}\n"


def build_artifacts(meta, directory, current_files, context, created):
    baseline = meta["versions"][0]
    with zipfile.ZipFile(directory / "versions" / (baseline["id"] + ".zip")) as archive:
        originals = {item.filename: item for item in archive.infolist() if not item.is_dir()}
        return _build_artifacts(meta, directory, current_files, context, created, baseline, archive, originals)


def stream_hash(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(64 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def _build_artifacts(meta, directory, current_files, context, created, baseline, archive, original):
    events = []
    versions = [{key: version[key] for key in ("id", "created", "label", "revision")} for version in meta['versions']]
    event_bytes = 0
    omitted = 0
    for version in reversed(meta["versions"]):
        if len(events) >= 500 and 'changeCount' in version:
            omitted += version['changeCount']
            continue
        event_file = directory / 'versions' / (version['id'] + '.changes.json')
        changes = json.loads(event_file.read_text(encoding='utf8')) if event_file.is_file() else version.get('changes', [])
        for change in reversed(changes):
            if isinstance(change, dict):
                size = len(json.dumps(change, ensure_ascii=False).encode('utf8'))
                if len(events) >= 500 or event_bytes + size > 2 * 1024 * 1024:
                    omitted += 1
                else:
                    events.append({**change, "version": version["id"], "savedAt": version["created"]})
                    event_bytes += size
    events.reverse()
    patches, inventory = [], []
    diff_bytes = 0
    for name in sorted(original.keys() | current_files.keys()):
        item, current = original.get(name), current_files.get(name)
        with archive.open(item) if item else io.BytesIO() as source:
            before_hash = stream_hash(source) if item else None
        if isinstance(current, Path):
            with current.open('rb') as source:
                after_hash = stream_hash(source)
            after_size = current.stat().st_size
        else:
            after_hash = hashlib.sha256(current).hexdigest() if current is not None else None
            after_size = len(current or b'')
        status = "added" if item is None else "deleted" if current is None else "unchanged" if before_hash == after_hash else "modified"
        inventory.append({"path": name, "status": status,
                          "beforeSha256": before_hash, "afterSha256": after_hash})
        if status == "unchanged":
            continue
        patch = {"path": name, "status": status}
        # Bound text diffs for decks with large embedded images; list every omitted file explicitly.
        if name.rsplit(".", 1)[-1].lower() not in TEXT_TYPES or max(item.file_size if item else 0, after_size) > 2 * 1024 * 1024:
            patch["note"] = "图片、二进制或大文件的内容未包含；请使用完整编辑包获取当前文件。"
        else:
            try:
                before = archive.read(item) if item else b''
                after = current.read_bytes() if isinstance(current, Path) else current or b''
                before_text, after_text = (before or b"").decode("utf-8-sig"), (after or b"").decode("utf-8-sig")
                diff = "\n".join(difflib.unified_diff(before_text.splitlines(), after_text.splitlines(), fromfile="original/" + name, tofile="document/" + name, lineterm=""))
                size = len(diff.encode("utf-8"))
                if diff_bytes + size > 8 * 1024 * 1024:
                    patch["note"] = "文字差异已达 8 MB；此文件请查看完整编辑包。"
                else:
                    patch["diff"] = diff
                    diff_bytes += size
            except UnicodeError:
                patch["note"] = "此文件不是 UTF-8 文本；请查看完整编辑包。"
        patches.append(patch)
    scope = context.get("scope", "current")
    if scope not in SCOPES:
        raise ValueError("请选择有效的检查范围")
    artifact = {"format": "deckroom.edits", "schemaVersion": 1, "created": created,
                "deck": {"name": meta["name"], "entry": meta["entry"], "localEntryPath": str(directory / "document" / meta["entry"])},
                "baseline": {"version": baseline["id"], "revision": baseline["revision"], "description": "首次导入的原稿"},
                "current": {"version": meta["versions"][-1]["id"], "revision": meta["revision"]},
                "intent": str(context.get("intent", ""))[:10000], "scope": scope, "scopeDescription": SCOPES[scope],
                "selection": context.get("selection"), "editEvents": events, "versions": versions,
                "omittedHistoricalEvents": omitted,
                "files": inventory, "patches": patches,
                "interpretation": "editEvents 是已保存操作的历史，可能包含后来被版本恢复撤销的操作。patches 才是首次导入至当前文件的最终差异。当前 HTML 已包含这些编辑，不要重复施加位移。"}
    summary = {k: v for k, v in artifact.items() if k != "patches"}
    markdown = (f"# Jemu · 简幕 编辑痕迹\n\n稿件：{meta['name']}\n\n"
                f"用户意图：{artifact['intent'] or '请根据编辑示例理解我的排版意图。'}\n\n"
                f"检查范围：{SCOPES[scope]}\n\n"
                "## 交给 AI 的说明\n\n"
                "请把此文件作为用户提供的编辑上下文，结合原稿或完整编辑包继续工作。"
                "仅编辑痕迹不含完整稿件与图片；若当前对话没有原稿，请先获取原稿或完整编辑包，不要凭空重建内容。\n\n"
                "按元素语义、页面结构和阅读顺序理解意图，不要把一页的位移直接复制给所有页面。"
                "跨页修复先列出受影响页面和方案；当前页的明确修改可直接完成。"
                "保留 logo 比例、data-slide-id / data-node-id、演讲稿、翻页和打印功能。\n\n"
                + artifact["interpretation"] + "\n\n"
                + (f"为控制导出体积，仅包含最近最多 500 条、2 MB 的详细操作，另有 {omitted} 条仍保存在本地版本记录中；最终文件差异继续覆盖全稿。\n\n" if omitted else '')
                + "完整编辑包中的 `document/` 是当前成稿，可直接打开；本机路径仅供同一台电脑定位。\n\n"
                "## 编辑上下文\n\n" + fenced(json.dumps(summary, ensure_ascii=False, indent=2), "json") + "\n"
                "## 相对导入原稿的最终差异\n\n")
    if not patches:
        markdown += "当前文件与首次导入的原稿一致，尚无有效文件差异。\n"
    for patch in patches:
        markdown += f"\n### {patch['path']} ({patch['status']})\n\n"
        markdown += fenced(patch["diff"], "diff") if "diff" in patch else patch["note"] + "\n"
    return artifact, markdown


def full_bundle(artifact, markdown, current_files, target=None):
    buffer = target if target is not None else io.BytesIO()
    readme = ("# Jemu · 简幕 完整编辑包\n\n"
              f"主文件：document/{artifact['deck']['entry']}\n\n"
              "1. 解压后用浏览器打开主文件，即可查看当前成稿。请保持 document 内的素材相对路径。\n"
              "2. 把整个 ZIP 交给 AI，并说明要继续处理；或解压后把文件夹交给可访问本机文件的 AI。\n"
              "3. edits.md 是可阅读的编辑痕迹和用户意图；changes.json 是结构化的同一份上下文。\n"
              "4. 当前稿件已经应用了记录中的编辑，请勿再次重复位移；结合最终差异理解用户意图。\n\n"
              "本包包含当前 HTML、素材与编辑上下文；完整版本历史仍保存在本地工作间。\n")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", readme)
        archive.writestr("edits.md", markdown)
        archive.writestr("changes.json", json.dumps(artifact, ensure_ascii=False, indent=2))
        for name, content in current_files.items():
            if isinstance(content, Path):
                archive.write(content, 'document/' + name)
            else:
                archive.writestr("document/" + name, content)
    return buffer.getvalue() if target is None else target
