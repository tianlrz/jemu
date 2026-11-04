# 版本记录

## 未发布

- macOS DMG：CI 构建 Apple 芯片与 Intel 两个 `Jemu-<版本>-<架构>.dmg`（Actions 产物；推送 `v*` 标签时附加到 Release）。App 自带精简 Python 3.12 与 PPT 组件，界面和 PDF 导出使用系统 WebKit 原生外壳，无需另装 Python、Chrome 或 Edge。未经 Apple 签名公证。
- App 内运行时，工作库与运行文件放在 `~/Library/Application Support/Jemu/`；文件夹方式仍在应用目录。`JEMU_HOME` 可覆盖。
- Python 查找优先 python.org 与 Homebrew 位置，Finder 启动时也能找到。

## 未发布

- macOS 预览支持：`Start.command` 双击启动、`./jemu` CLI；自动寻找 `/Applications`、`~/Applications` 中的 Chrome／Edge／Chromium，用于独立应用窗口和 PDF／PPT 导出。
- macOS 演示使用浏览器系统全屏；Windows 无边框窗口控制不变。
- macOS 坚果云应用密码保存在登录钥匙串，通过 Security.framework 内存传递，不出现在命令行参数中；断开关联时一并删除。
- 「打开文件夹」在 macOS 使用 Finder；非 Windows 导出进程按进程组结束，避免遗留浏览器子进程。
- CI 增加 macOS 运行器；新增 tests/macos_support_test.py。

## v0.1.0-preview.2 · 2026-10-16

- 固定导入文件与界面脚本的 `.js`／`.mjs` MIME 类型，避免 Windows 文件关联将其标记为普通文本，导致模块被浏览器拒绝加载。
- 增加错误系统 MIME 映射的回归验证。

## v0.1.0-preview.1 · 2026-10-16

首次公开预览。

- Windows 桌面无边框全屏：隐藏编辑界面、填满所在屏幕；Esc 恢复原窗口大小与位置。
- 独立演讲者窗口：讲稿、下一页标题、双向翻页、计时；可从稿窗结束演示。
- 本地 HTML 工作库：首次准备持久 Jemu 工作版本，保留原稿与每次保存的历史，避免每次加载重复转换。
- PPT 式轻编辑：选框拖动、缩放、复制粘贴、字体、撤销／重做；导出编辑痕迹或完整编辑包。
- 展示与导出分开：整稿 PDF、PDF 逐页图片式 PPTX，导出进程按需运行并释放。
- 独立设置页：坚果云 WebDAV 与 Google Drive／OneDrive／夸克等本地同步目录存档。
- CLI 与 MCP stdio：设置、文件、版本、导出、备份与窗口操作共用本地 API。

预览边界：任意动态 HTML 不保证完整适配；图片式 PPT 无逐字编辑和动画；动态效果与 GitHub 发布到 Pages 的应用内入口尚未实现。坚果云只做本机模拟服务测试，未使用真实账户进行自动测试。原生窗口控制暂仅 Windows，其他浏览器依照自身全屏和弹窗限制运行。
