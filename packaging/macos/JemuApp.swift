// Jemu.app native shell: hosts the local Jemu service in the system WebKit (no Chrome/Edge needed).
//
//   Jemu                         start or reuse the bundled service and open the workspace window
//   Jemu --render-pdf OUT W H N ENTRY ROOT
//                                export N pages of W×H CSS px from a static HTML file to a PDF (used by
//                                render_exports.py); runs headless and exits.
//
// Only the bundled Python and system frameworks are used. Nothing is downloaded at run time.
import Cocoa
import WebKit

let resources = Bundle.main.resourceURL!
let appCode = resources.appendingPathComponent("app")
let pythonURL = resources.appendingPathComponent("python/bin/python3")

func jemuHome() -> URL {
    if let value = ProcessInfo.processInfo.environment["JEMU_HOME"], !value.isEmpty {
        return URL(fileURLWithPath: (value as NSString).expandingTildeInPath).standardizedFileURL
    }
    return FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Jemu")
}

func isLocal(_ url: URL) -> Bool {
    if ["about", "blob", "data"].contains(url.scheme ?? "") { return true }
    return url.scheme == "http" && (url.host == "127.0.0.1" || url.host == "localhost")
}

// MARK: - Headless PDF export

final class PDFRenderer: NSObject, WKNavigationDelegate {
    let output: URL, width: CGFloat, height: CGFloat, pages: Int, entry: URL, root: URL
    var webView: WKWebView!
    var window: NSWindow!

    init(arguments: [String]) {
        guard arguments.count == 8, let w = Double(arguments[3]), let h = Double(arguments[4]), let n = Int(arguments[5]),
              w >= 100, h >= 100, w <= 10000, h <= 10000, (1...200).contains(n) else {
            FileHandle.standardError.write("usage: Jemu --render-pdf OUT WIDTH HEIGHT PAGES ENTRY ROOT\n".data(using: .utf8)!)
            exit(2)
        }
        output = URL(fileURLWithPath: arguments[2]); width = CGFloat(w); height = CGFloat(h); pages = n
        entry = URL(fileURLWithPath: arguments[6]); root = URL(fileURLWithPath: arguments[7])
    }

    func fail(_ message: String) -> Never {
        FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
        exit(1)
    }

    func start() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false  // export pages are static
        let total = height * CGFloat(pages)
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: total), configuration: config)
        webView.navigationDelegate = self
        // Kept off screen and never shown; WebKit still lays the page out at the requested size.
        window = NSWindow(contentRect: NSRect(x: -40000, y: -40000, width: width, height: min(total, 4000)),
                          styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView?.addSubview(webView)
        DispatchQueue.main.asyncAfter(deadline: .now() + 50) { self.fail("render timeout") }
        webView.loadFileURL(entry, allowingReadAccessTo: root)
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        // The initial load is allowed; links or forms in the export page never navigate away.
        decisionHandler([.linkActivated, .formSubmitted, .formResubmitted].contains(action.navigationType) ? .cancel : .allow)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { fail("load failed: \(error)") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { fail("load failed: \(error)") }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let ready = """
        await document.fonts.ready;
        await Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        return 1;
        """
        webView.callAsyncJavaScript(ready, arguments: [:], in: nil, in: .defaultClient) { _ in self.capture(from: 0, into: nil) }
    }

    // Captures at most ~10,000 px per PDF call, then slices each capture into exact W×H pages.
    func capture(from first: Int, into context: CGContext?) {
        var context = context
        if context == nil {
            var box = CGRect(x: 0, y: 0, width: width, height: height)
            guard let made = CGContext(output as CFURL, mediaBox: &box, nil) else { fail("cannot create \(output.path)") }
            context = made
        }
        guard let ctx = context else { return }
        if first >= pages { ctx.closePDF(); exit(0) }
        let count = min(pages - first, max(1, Int(10000 / height)))
        let config = WKPDFConfiguration()
        config.rect = CGRect(x: 0, y: CGFloat(first) * height, width: width, height: CGFloat(count) * height)
        webView.createPDF(configuration: config) { result in
            guard case .success(let data) = result, let provider = CGDataProvider(data: data as CFData),
                  let document = CGPDFDocument(provider), document.numberOfPages >= 1 else { self.fail("WebKit did not produce a PDF") }
            if document.numberOfPages == count {
                for index in 1...count { self.emit(document.page(at: index)!, slice: 0, of: 1, into: ctx) }
            } else {
                let page = document.page(at: 1)!
                for slice in 0..<count { self.emit(page, slice: slice, of: count, into: ctx) }
            }
            self.capture(from: first + count, into: ctx)
        }
    }

    func emit(_ page: CGPDFPage, slice: Int, of count: Int, into ctx: CGContext) {
        let source = page.getBoxRect(.mediaBox)
        let sliceHeight = source.height / CGFloat(count)
        var box = CGRect(x: 0, y: 0, width: source.width, height: sliceHeight)
        ctx.beginPage(mediaBox: &box)
        ctx.saveGState()
        // PDF space grows upwards: slice 0 is the top of the capture.
        ctx.translateBy(x: -source.minX, y: -(source.maxY - CGFloat(slice + 1) * sliceHeight))
        ctx.drawPDFPage(page)
        ctx.restoreGState()
        ctx.endPage()
    }
}

// MARK: - Workspace windows

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    var server: Process?
    var windows: [NSWindow] = []
    var titleObservers: [ObjectIdentifier: NSKeyValueObservation] = [:]
    var terminating = false
    var confirmedClose = Set<ObjectIdentifier>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let home = jemuHome()
        try? FileManager.default.createDirectory(at: home.appendingPathComponent(".runtime"), withIntermediateDirectories: true)
        reuseService(home: home) { url in
            if let url = url { self.openWindow(url: url, size: NSSize(width: 1440, height: 940), configuration: nil); return }
            self.startService(home: home)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating || windows.isEmpty { return .terminateNow }
        unsavedWindows { dirty in
            let quit = dirty.isEmpty || self.confirm("有未保存的修改", "退出简幕会放弃这些修改。仍要退出吗？", "退出")
            if quit { self.terminating = true }
            sender.reply(toApplicationShouldTerminate: quit)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard let server = server, server.isRunning else { return }
        server.interrupt()  // SIGINT: the service stops export jobs and closes cleanly
        let deadline = Date().addingTimeInterval(3)
        while server.isRunning && Date() < deadline { usleep(50_000) }
        if server.isRunning { server.terminate() }
    }

    // A service started earlier by this app (same library) is reused.
    func reuseService(home: URL, done: @escaping (URL?) -> Void) {
        let session = home.appendingPathComponent(".runtime/session.json")
        guard let data = try? Data(contentsOf: session),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = json["url"] as? String, let url = URL(string: text), isLocal(url) else { return done(nil) }
        var request = URLRequest(url: url.appendingPathComponent("api/health")); request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { body, _, _ in
            let health = body.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            let library = home.appendingPathComponent("library").resolvingSymlinksInPath().path
            let same = health?["app"] as? String == "deckroom" &&
                (health?["library"] as? String).map { URL(fileURLWithPath: $0).resolvingSymlinksInPath().path == library } == true
            DispatchQueue.main.async { done(same ? url : nil) }
        }.resume()
    }

    func startService(home: URL) {
        let process = Process()
        process.executableURL = pythonURL
        // -E -s: ignore PYTHON* variables and user site-packages; -B: never write caches into the bundle.
        process.arguments = ["-E", "-s", "-B", appCode.appendingPathComponent("server.py").path, "--port", "0"]
        var environment = ProcessInfo.processInfo.environment
        environment["JEMU_HOME"] = home.path
        environment["JEMU_NATIVE_SHELL"] = "1"
        environment["JEMU_RENDERER"] = Bundle.main.executablePath
        process.environment = environment
        let runtime = home.appendingPathComponent(".runtime")
        let errorLog = runtime.appendingPathComponent("error.log")
        if !FileManager.default.fileExists(atPath: errorLog.path) { FileManager.default.createFile(atPath: errorLog.path, contents: nil) }
        if let handle = try? FileHandle(forWritingTo: errorLog) { handle.seekToEndOfFile(); process.standardError = handle }
        let output = Pipe(); process.standardOutput = output
        var opened = false, buffer = Data()
        output.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty { handle.readabilityHandler = nil; return }
            if opened { return }  // later output is not needed; keep draining so the pipe never fills
            buffer.append(chunk)
            guard let line = String(data: buffer, encoding: .utf8)?.split(separator: "\n").first,
                  let url = URL(string: String(line).trimmingCharacters(in: .whitespaces)), isLocal(url) else { return }
            opened = true
            DispatchQueue.main.async { self.openWindow(url: url, size: NSSize(width: 1440, height: 940), configuration: nil) }
        }
        process.terminationHandler = { _ in
            DispatchQueue.main.async {
                if !opened { self.showStartupError(log: errorLog) }
                // 「退出服务」 in the workspace stops the service; the app follows.
                self.terminating = true
                NSApp.terminate(nil)
            }
        }
        do { try process.run() } catch { showStartupError(log: errorLog); NSApp.terminate(nil) }
        server = process
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) {
            if !opened && process.isRunning { process.terminate() }
        }
    }

    func showStartupError(log: URL) {
        let alert = NSAlert()
        alert.messageText = "简幕没有启动"
        alert.informativeText = "本地服务未能启动。详细信息见：\n" + log.path
        alert.runModal()
    }

    @discardableResult
    func openWindow(url: URL?, size: NSSize, configuration: WKWebViewConfiguration?) -> WKWebView {
        let config = configuration ?? WKWebViewConfiguration()
        if configuration == nil {
            config.preferences.javaScriptCanOpenWindowsAutomatically = true
            if #available(macOS 12.3, *) { config.preferences.isElementFullscreenEnabled = true }
        }
        let webView = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        if #available(macOS 13.3, *) { webView.isInspectable = true }
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "简幕"
        window.contentView = webView
        window.collectionBehavior = [.fullScreenPrimary]
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.setFrameAutosaveName(windows.isEmpty ? "JemuWorkspace" : "")
        if windows.isEmpty { window.center() } else if let last = windows.last { window.setFrameTopLeftPoint(NSPoint(x: last.frame.minX + 30, y: last.frame.maxY - 30)) }
        titleObservers[ObjectIdentifier(window)] = webView.observe(\.title) { view, _ in
            if let title = view.title, !title.isEmpty { view.window?.title = title }
        }
        windows.append(window)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if let url = url { webView.load(URLRequest(url: url)) }
        return webView
    }

    // The pages mark unsaved drafts by cancelling beforeunload; ask them the same question before closing.
    func hasUnsaved(_ webView: WKWebView, done: @escaping (Bool) -> Void) {
        let script = "(() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; })()"
        webView.evaluateJavaScript(script) { result, _ in done(result as? Bool == true) }
    }

    func unsavedWindows(done: @escaping ([NSWindow]) -> Void) {
        let group = DispatchGroup(); var dirty: [NSWindow] = []
        for window in windows {
            guard let view = window.contentView as? WKWebView else { continue }
            group.enter()
            hasUnsaved(view) { unsaved in if unsaved { dirty.append(window) }; group.leave() }
        }
        group.notify(queue: .main) { done(dirty) }
    }

    func confirm(_ title: String, _ text: String, _ action: String) -> Bool {
        let alert = NSAlert()
        alert.messageText = title; alert.informativeText = text
        alert.addButton(withTitle: action); alert.addButton(withTitle: "取消")
        return alert.runModal() == .alertFirstButtonReturn
    }

    func windowShouldClose(_ window: NSWindow) -> Bool {
        let key = ObjectIdentifier(window)
        if terminating || confirmedClose.contains(key) { return true }
        guard let view = window.contentView as? WKWebView else { return true }
        hasUnsaved(view) { dirty in
            if !dirty || self.confirm("有未保存的修改", "关闭窗口会放弃这些修改。仍要关闭吗？", "关闭") {
                self.confirmedClose.insert(key); window.close()
            }
        }
        return false
    }

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        confirmedClose.remove(ObjectIdentifier(window)); titleObservers.removeValue(forKey: ObjectIdentifier(window))
        (window.contentView as? WKWebView)?.stopLoading()
        window.contentView = nil  // release the page and its WebKit process
        windows.removeAll { $0 === window }
    }

    // window.open: settings page and presenter window open as Jemu windows; other sites go to the default browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, !isLocal(url) { NSWorkspace.shared.open(url); return nil }
        let width = windowFeatures.width.map { CGFloat($0.doubleValue) } ?? 1200
        let height = windowFeatures.height.map { CGFloat($0.doubleValue) } ?? 860
        return openWindow(url: nil, size: NSSize(width: max(480, width), height: max(360, height)), configuration: configuration)
    }

    func webViewDidClose(_ webView: WKWebView) {
        if let window = webView.window { confirmedClose.insert(ObjectIdentifier(window)); window.close() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { return decisionHandler(.download) }
        if let url = action.request.url, action.targetFrame?.isMainFrame != false, !isLocal(url), ["http", "https", "mailto"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url); return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true
        decisionHandler(response.canShowMIMEType && !attachment ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    // Downloads (PDF/PPT/ZIP/drafts) go to ~/Downloads with a unique name, like a browser.
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let name = (suggestedFilename as NSString).lastPathComponent
        let base = (name as NSString).deletingPathExtension, ext = (name as NSString).pathExtension
        var target = folder.appendingPathComponent(name.isEmpty ? "jemu-download" : name), index = 2
        while FileManager.default.fileExists(atPath: target.path) {
            target = folder.appendingPathComponent(ext.isEmpty ? "\(base) \(index)" : "\(base) \(index).\(ext)"); index += 1
        }
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let path = download.progress.fileURL?.path {
            DistributedNotificationCenter.default().post(name: .init("com.apple.DownloadFileFinished"), object: path)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let alert = NSAlert(); alert.messageText = "下载没有完成"; alert.informativeText = error.localizedDescription; alert.runModal()
    }

    // <input type=file> including folder import (webkitdirectory).
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = !parameters.allowsDirectories
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection && !parameters.allowsDirectories
        guard let window = webView.window else { return completionHandler(panel.runModal() == .OK ? panel.urls : nil) }
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.runModal(); completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert(); alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24)); field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "确定"); alert.addButton(withTitle: "取消")
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem(); let menu = NSMenu(title: title)
            for entry in items { menu.addItem(entry) }; holder.submenu = menu; main.addItem(holder)
        }
        func item(_ title: String, _ action: Selector?, _ key: String, _ modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: key); entry.keyEquivalentModifierMask = modifiers; return entry
        }
        submenu("简幕", [
            item("关于简幕", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(),
            item("隐藏简幕", #selector(NSApplication.hide(_:)), "h"),
            item("隐藏其他", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("全部显示", #selector(NSApplication.unhideAllApplications(_:)), ""),
            .separator(),
            item("退出简幕", #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu("编辑", [
            item("撤销", Selector(("undo:")), "z"),
            item("重做", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("剪切", #selector(NSText.cut(_:)), "x"),
            item("拷贝", #selector(NSText.copy(_:)), "c"),
            item("粘贴", #selector(NSText.paste(_:)), "v"),
            item("全选", #selector(NSText.selectAll(_:)), "a"),
        ])
        submenu("显示", [item("进入全屏", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])])
        submenu("窗口", [
            item("最小化", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("关闭窗口", #selector(NSWindow.performClose(_:)), "w"),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "--render-pdf" {
    app.setActivationPolicy(.prohibited)
    let renderer = PDFRenderer(arguments: CommandLine.arguments)
    renderer.start()
    app.run()
} else {
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
