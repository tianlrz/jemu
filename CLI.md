# 简幕 CLI

此文档面向 AI／开发兼容入口。用户使用独立设置页；AI 首选 [MCP](MCP.md)，无需让用户运行或理解 CLI。界面、MCP 与 CLI 使用同一个本地服务、设置和版本库。普通文件操作不需要打开浏览器；CLI 不模拟鼠标、不连接大模型、不内置终端。仅使用 Python 标准库。

## 入口与结果

在应用目录使用 `./jemu.cmd`（PowerShell 用 `.\jemu.cmd`）。跨目录可以使用完整路径，或自行把应用目录加入 PATH。其他系统用 `python jemu.py`，凭据加密保存目前仍仅支持 Windows。

```powershell
.\jemu.cmd start
.\jemu.cmd status
.\jemu.cmd capabilities --json
.\jemu.cmd --help
```

`start` 启动无窗口本地服务，复用已运行的实例；`start --desktop` 可在新启动时打开应用窗口。默认从应用目录 `.runtime/session.json` 发现服务。连接其他本机实例用 `--url http://127.0.0.1:端口`，放在子命令之前；不能连接远程服务器，不跟随 HTTP 重定向，不输出会话令牌。

`desktop --deck 作品ID` 可在已运行的服务上显式打开 Windows 桌面窗口；MCP 为 `jemu_service` 的 `desktop` 操作。`ui status` 返回 `desktop`、`fullscreen` 与窗口会话标识。桌面窗口可通过原有 `ui present`／`ui exit` 进入无边框全屏与恢复，普通浏览器仍受用户激活限制。`GET /api/window/status?window=会话标识` 可核对真实窗口边界、所在显示器与边框状态。只接受启动器注册过的当前应用窗口，不接受任意 PID／HWND。

常规命令输出 JSON：`{"ok":true,"data":...}` 或 `{"ok":false,"error":{"code":"...","message":"..."}}`。`--json` 输出紧凑 JSON，可放在子命令前或后。`--help` 输出人类可读帮助。文件默认不覆盖，需明确 `--overwrite`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 参数／输入／输出路径问题 |
| 3 | 服务未启动或本机连接失败 |
| 4 | 文件版本冲突，或停止服务时检测到未保存编辑 |
| 5 | 操作失败或界面命令等待超时，读取错误码与详情 |
| 130 | 用户中断；先检查状态再重试 |

`capabilities` 返回 API 版本、能力、界面命令和限制。`request GET /api/...`、`request POST /api/... --body 文件.json` 提供底层接口入口；`--body -` 从标准输入读 JSON。只允许本地 `/api/` 路径，不执行任意 JavaScript。所有修改接口仍使用当前本机会话验证。

## 设置与备份

```powershell
.\jemu.cmd settings get
.\jemu.cmd settings backup get
.\jemu.cmd settings backup set --provider onedrive --folder 'D:\MyCloud\Jemu'
.\jemu.cmd settings backup set --provider quark --folder 'D:\QuarkBackup'
.\jemu.cmd settings backup set --provider nutstore --username '你的账号邮箱' --prompt-password
.\jemu.cmd settings backup test
.\jemu.cmd settings backup disconnect
```

目录需先存在。文件夹适配支持 `google-drive`、`onedrive`、`quark`、`dropbox`、`folder`；云盘客户端负责上传。坚果云使用第三方应用密码，通过 `--prompt-password` 隐藏输入，或 `--password-stdin` 从标准输入传入；不提供明文密码命令行参数，避免进入命令历史和进程参数。已经保存过同一账号时，可省略密码选项保留原密码。不要在聊天中发送真实密码。

独立设置页与 AI 工具共用 `/api/settings/backup`。打开的设置页在前台每五秒检查一次配置版本；若有未保存输入则保留输入并提示冲突。界面提交附带配置版本，避免覆盖 AI 工具的新配置或备份到意外位置。直接调用接口时可带 `expectedSettingsRevision` 做同样的冲突检查。

```powershell
.\jemu.cmd backup create deck-作品ID
.\jemu.cmd backup list --month 2026-10
.\jemu.cmd backup restore 20261015T120000Z-0123456789ab.jemu.zip
```

这些是手动存档，保留完整历史。文件夹适配只报告本机已写入；坚果云在服务器确认接收后报告已上传。恢复创建新副本。`--month` 是坚果云的 UTC 月份，文件夹列表不按月过滤；`--offset 50` 获取下一页。

## 文件、版本与导出

`jemu export deck-ID --kind pdf --out D:\\Exports\\deck.pdf` 导出独立整稿 PDF；`--kind pptx` 导出 PDF 逐页图片组成的 PPTX。MCP 的 `jemu_export` 使用相同 kind。需要保存好的明确页组（`data-jemu-deck` 或旧 `deck-stage`），动态稿先在窗口打开完成准备。不会改变演示窗口当前页；PPT 文字不能逐字编辑。PDF 需要系统 Edge／Chromium，PPT 另需可选 pypdfium2／Pillow；它们只在导出子进程使用。`GET /api/render-capabilities` 可发现可用性。

`POST /api/decks/{id}/render-start` 接受 kind、baseRevision；可选 entry，或由界面提供去除运行脚本的完整 html／pages／width／height。返回任务编号；`GET /api/decks/{id}/render-jobs/{job}` 查询，完成后访问其 downloadUrl。窗口之外的 CLI／MCP 直接从保存的页组构建版式，不依赖打开的窗口。任务一次一项，返回文件到磁盘，不将整稿图片作为 JSON/Base64 回传。超时先查询状态，不自动重复发起。

```powershell
.\jemu.cmd decks import 'D:\Meeting' --entry index.html --name '组会'
.\jemu.cmd decks list
.\jemu.cmd decks show deck-作品ID
.\jemu.cmd decks mode deck-作品ID native
.\jemu.cmd files list deck-作品ID
.\jemu.cmd files read deck-作品ID index.html
.\jemu.cmd files read deck-作品ID assets/logo.png --out 'D:\Draft\logo.png'
```

目录导入包含 HTML 和素材；默认跳过隐藏文件／目录和 `__pycache__`。优先识别 `index.html`，否则仅有一个 HTML 时选它，有多个则需指定 `--entry`。独立 HTML 文件也可直接导入。当前稿件总量仍限 100 MiB；CLI 不会绕过素材大小限制。

导入立即保存原稿快照；首次界面打开会创建持久的 Jemu 工作版本，动态 deck 的完整转换在这时执行，不额外启动后台浏览器。`decks prepare deck-ID --base-revision REV`（MCP 为 `jemu_decks` 的 `prepare` 操作）不改文件字节，只创建一次工作版本快照；可选 `--source 完整稿件.html --entry index.html` 提交 AI 已准备的完整 UTF-8 HTML（最多 10 MiB）。接口为 `POST /api/decks/{id}/prepare`，接受 `baseRevision`、可选 `entry` 和可选完整 `html`。先保证源稿有快照，再保存工作版本，重复相同内容不增加快照，冲突返回 409。返回的 `jemuVersion` 包含来源版本、工作版本及对应文件版本。

文件写入使用完整文件和明确的基础版本。先读取当前稿件信息，再修改导出的草稿文件，再提交；如果期间有其他修改，返回 `conflict`（退出码 4），原文件不会被覆盖。每次成功修改自动留版本。

```powershell
$deck = (.\jemu.cmd --json decks show deck-作品ID | ConvertFrom-Json).data
.\jemu.cmd files write $deck.id index.html --from 'D:\Draft\index.html' --base-revision $deck.revision
.\jemu.cmd versions list $deck.id
# 删除素材同样要求新读取的版本；入口文件不可删除
.\jemu.cmd files delete $deck.id assets/old.png --base-revision 新读取的revision
.\jemu.cmd versions restore $deck.id 版本ID --base-revision 新读取的revision
```

HTML、CSS、JS、图片等都可读写。大文件分块传输、在磁盘暂存后原子替换，不转为 Base64。也可以让 AI 直接编辑 `decks show` 返回的 `entryPath`／`path`；下一次读取稿件或界面检测时，会自动记录外部修改。已有未保存界面草稿保留，保存时按版本检查。

```powershell
.\jemu.cmd export deck-作品ID --kind source --out 'D:\Export\meeting.zip'
.\jemu.cmd export deck-作品ID --kind trace --out 'D:\Export\edits.md' --intent '统一类似页面的结论位置' --scope similar
.\jemu.cmd export deck-作品ID --kind full --out 'D:\Export\editing.zip'
```

`source` 是可独立展示的 HTML 和素材；`trace` 是给 AI 的编辑痕迹；`full` 是当前作品加编辑痕迹的完整编辑包。云备份另外包含全部版本。CLI 文件导出使用磁盘流，不要求作品在窗口中打开。

## 控制演示窗口

```powershell
.\jemu.cmd ui status
.\jemu.cmd ui open deck-作品ID
.\jemu.cmd ui goto 3
.\jemu.cmd ui next
.\jemu.cmd ui previous
.\jemu.cmd ui select '[data-node-id="某个元素ID"]'
.\jemu.cmd ui move 0 80
.\jemu.cmd ui format --font 'Microsoft YaHei' --size 30 --bold on
.\jemu.cmd ui text --from 'D:\Draft\title.txt'
.\jemu.cmd ui undo
.\jemu.cmd ui redo
.\jemu.cmd ui save
.\jemu.cmd ui settings
```

`ui status` 返回窗口 ID、当前作品、页码、未保存状态、选中元素的 CSS 选择器／文字／位移／行内样式和计时状态，避免依靠截图推断。页码从 1 开始，自由网页不伪造页码。`select` 必须精确匹配一个元素；纯文本元素可以改字。`move x y` 设置相对原始布局的绝对位移（稿件像素），与界面属性栏一致。容器结构编辑用文件接口。

还支持 `ui edit on|off`、`copy`、`paste`、`delete`、`reset`、`present`、`exit`、`presenter`、`timer start|pause|reset`。复制粘贴使用简幕内存中的对象剪贴板，不读写系统剪贴板。UI 操作进入同一套撤销／重做历史；只有 `ui save` 才写入磁盘。`ui open` 遇到未保存编辑会拒绝切换，先保存后再打开。`present` 和 `presenter` 可能被浏览器拦截弹窗，此时明确返回失败；PDF 打印、浏览器全屏仍依赖窗口和浏览器权限，当前没有无界面 PDF 导出。GitHub 发布尚未实现。

有多个窗口时，用 `--client 窗口ID` 明确目标；可以附带 `--expect-deck`、`--expect-revision`，防止操作落到已切换的作品上。当前最多跟踪 4 个窗口。状态每秒一次，隐藏窗口降为每五秒；浏览器自身仍可能进一步节流。只同步小状态和命令，不读取全稿／详细历史／讲稿或复制渲染进程。

命令默认等待执行结果，`--timeout 15` 调整等待秒数，`--no-wait` 立即返回命令编号，之后 `ui result 编号` 查询。结果最多保留两分钟、64 条，每窗口最多 16 条未完成命令。未送达的命令 30 秒后失效。不自动重发已交付的命令：超时或连接中断不代表未执行，先查结果／状态再决定是否重试。CLI 退出不会新增常驻辅助进程。

`stop` 检查最近上报的界面草稿状态，有未保存编辑时拒绝停止；明确使用 `stop --force` 可以停止服务。状态不是瞬时锁；停止前应先完成保存。

## 验证

`python tests/cli_test.py` 覆盖独立服务生命周期、设置、分块导入／文件修改、旧版本冲突、历史恢复、三种导出、输出不覆盖、备份恢复，以及命令队列与窗口缓存上限。所有测试使用临时作品库，不接触真实账户或本机稿件库。实际界面另验证选择、改字、字体、位移、撤销、重做、保存和 CLI 设置更新。
