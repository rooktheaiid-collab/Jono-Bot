#!/bin/bash
# Instinct Voice for macOS: builds and installs the menu-bar app. Needs Xcode Command Line Tools (swiftc).
# Usage: bash install.sh [--login] [--uninstall]
set -euo pipefail
APP="$HOME/Applications/InstinctVoice.app"
PLIST="$HOME/Library/LaunchAgents/id.reza.voicemenu.plist"
LOGIN=0
for a in "$@"; do
  case "$a" in
    --login) LOGIN=1 ;;
    --uninstall)
      pkill -x InstinctVoice 2>/dev/null || true
      rm -rf "$APP" "$PLIST"
      echo "Dihapus. (Izin mikrofon bisa direset: tccutil reset Microphone id.reza.voicemenu)"
      exit 0 ;;
  esac
done
[ "$(uname -s)" = "Darwin" ] || { echo "Script ini cuma buat macOS."; exit 1; }
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Command Line Tools belum ada. Jendela install akan muncul; selesaikan, lalu jalankan script ini lagi."
  xcode-select --install || true
  exit 1
fi
ARCH="$(uname -m)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/main.swift" <<'SWIFT_EOF'
// Instinct Voice for macOS v3: tiny menu-bar app around WKWebView.
// Summon (hotkey / menu bar icon) = a pair of floating cartoon eyes that follow the cursor, voice session starts at once.
// Right-click or long-press the bubble = full panel (passcode, settings, history).
import Cocoa
import WebKit
import Carbon
import AVFoundation
import QuartzCore

let kURL = URL(string: "https://101-32-114-234.sslip.io/")!
let kBlankHTML = "<html><body style='background:#ffd84a'></body></html>"

// Injected into the page: reports the app state (body data-state / data-err) to native code.
let kStateJS = """
(function(){
  function s(){try{window.webkit.messageHandlers.st.postMessage((document.body.dataset.state||'idle')+'|'+(document.body.dataset.err||'0'));}catch(e){}}
  if(document.body){new MutationObserver(s).observe(document.body,{attributes:true,attributeFilter:['data-state','data-err']});s();}
})();
"""
let kStartJS = "(function(){var b=document.getElementById('b');if(!b)return 'nob';if(!localStorage.getItem('pc'))return 'nopc';document.body.dataset.err='0';if(!b.classList.contains('on'))b.click();return 'ok';})()"
let kStopJS = "(function(){var b=document.getElementById('b');if(b&&b.classList.contains('on'))b.click();})()"

final class Panel: NSPanel {
    override var canBecomeKey: Bool { return true }
    override var canBecomeMain: Bool { return true }
}

final class BubblePanel: NSPanel {
    override var canBecomeKey: Bool { return false }
    override var canBecomeMain: Bool { return false }
}

final class PassView: NSView {
    // Lets clicks fall through to the eyes drawn in the parent view.
    override func hitTest(_ point: NSPoint) -> NSView? { return nil }
}

// Two plain cartoon eyes drawn in code. Parameters (index): 0 topInnerL, 1 topOuterL, 2 topInnerR, 3 topOuterR
// (upper lid cover, fraction of eye height), 4 bottomL, 5 bottomR (lower lid cover), 6 cheek (smile bite),
// 7 scale, 8 pupil size, 9 pupil down bias, 10 wander, 11 heart pupil, 12 roundness, 13 pupils drift outward.
final class EyesView: NSView {
    override var isFlipped: Bool { return true }

    static let table: [String: [CGFloat]] = [
        "flat":    [0, 0, 0, 0, 0, 0, 0, 0.92, 0.42, 0, 0, 0, 0.32, 0],
        "normal":  [0, 0, 0, 0, 0, 0, 0, 1.0, 0.44, 0, 0, 0, 0.5, 0],
        "senang":  [0, 0, 0, 0, 0, 0, 1, 1.0, 0.0, 0, 0, 0, 0.5, 0],
        "sedih":   [0.0, 0.42, 0.0, 0.42, 0, 0, 0, 1.0, 0.5, 0.6, 0, 0, 0.45, 0],
        "serius":  [0.22, 0.22, 0.22, 0.22, 0, 0, 0, 0.95, 0.4, 0.15, 0, 0, 0.22, 0],
        "marah":   [0.5, 0.1, 0.5, 0.1, 0, 0, 0, 0.95, 0.38, 0.1, 0, 0, 0.28, 0],
        "bingung": [0, 0, 0.32, 0.32, 0, 0, 0, 1.0, 0.44, 0, 1, 0, 0.5, 0.45],
        "ngambek": [0.28, 0.28, 0.28, 0.28, 0.3, 0.3, 0, 0.95, 0.46, 0.45, 0, 0, 0.3, 0],
        "ngantuk": [0.5, 0.5, 0.5, 0.5, 0, 0, 0, 0.95, 0.5, 0.5, 0, 0, 0.3, 0],
        "kaget":   [0, 0, 0, 0, 0, 0, 0, 1.18, 0.2, 0, 0, 0, 0.5, 0],
        "cinta":   [0, 0, 0, 0, 0, 0, 0, 1.05, 0.78, 0, 0, 1, 0.5, 0]
    ]

    let holder = PassView(frame: NSRect(x: 68, y: 38, width: 24, height: 24))   // hidden home of the web view
    var onTap: (() -> Void)?
    var onMenu: (() -> Void)?
    var downAt = Date()

    var cur: [CGFloat] = EyesView.table["flat"]!
    var tgt: [CGFloat] = EyesView.table["flat"]!
    var exprName = "flat"
    var pos: [CGPoint] = [CGPoint(x: 0, y: 0), CGPoint(x: 0, y: 0)]
    var vel: [CGPoint] = [CGPoint(x: 0, y: 0), CGPoint(x: 0, y: 0)]
    var appear: CGFloat = 1.0
    var bobAmp: CGFloat = 0
    var bobY: CGFloat = 0
    var blink: CGFloat = 0
    var speaking = false
    var blinkStart: TimeInterval = -10
    var blinkDur: TimeInterval = 0.15
    var nextBlink: TimeInterval = 0
    var lastTick: TimeInterval = 0
    var timer: Timer?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        holder.wantsLayer = true
        holder.alphaValue = 0.02      // practically invisible, but still "on screen" so the page is not throttled
        addSubview(holder)
    }
    required init?(coder: NSCoder) { fatalError("not used") }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { return true }
    override func mouseDown(with event: NSEvent) { downAt = Date() }
    override func mouseUp(with event: NSEvent) {
        if Date().timeIntervalSince(downAt) > 0.5 { onMenu?() } else { onTap?() }
    }
    override func rightMouseUp(with event: NSEvent) { onMenu?() }

    func setExpr(_ name: String) {
        guard let t = EyesView.table[name] else { return }
        tgt = t
        exprName = name
    }

    func start() {
        appear = 0.6
        let now = ProcessInfo.processInfo.systemUptime
        lastTick = now
        nextBlink = now + 1.5
        if timer == nil {
            let tm = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in self?.tick() }
            RunLoop.main.add(tm, forMode: RunLoop.Mode.common)
            timer = tm
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func tick() {
        let now = ProcessInfo.processInfo.systemUptime
        var dt = now - lastTick
        lastTick = now
        if dt > 0.05 { dt = 0.05 }
        if dt <= 0 { return }
        let d = CGFloat(dt)
        let f = 1 - exp(-d / 0.09)                       // ~300 ms morph between expressions
        for i in 0..<cur.count { cur[i] += (tgt[i] - cur[i]) * f }
        appear += (1 - appear) * (1 - exp(-d / 0.1))
        bobAmp += ((speaking ? 1 : 0) - bobAmp) * (1 - exp(-d / 0.15))
        bobY = -abs(CGFloat(sin(now * Double.pi / 0.55))) * 5 * bobAmp

        // natural blinking
        let sleepy = (exprName == "ngantuk")
        if now >= nextBlink && now - blinkStart > blinkDur {
            blinkStart = now
            blinkDur = sleepy ? 0.34 : 0.15
            nextBlink = now + blinkDur + (sleepy ? Double.random(in: 1.5...3.0) : Double.random(in: 3.0...6.0))
        }
        let bp = (now - blinkStart) / blinkDur
        blink = (bp >= 0 && bp <= 1) ? CGFloat(sin(bp * Double.pi)) : 0

        updatePupils(d, now)
        needsDisplay = true
    }

    func eyeCenter(_ i: Int) -> CGPoint {
        return CGPoint(x: bounds.width / 2 + (i == 0 ? -34 : 34), y: bounds.height / 2 + bobY)
    }

    func updatePupils(_ d: CGFloat, _ now: TimeInterval) {
        let m = NSEvent.mouseLocation
        for i in 0..<2 {
            let c = eyeCenter(i)
            var lx: CGFloat = 0
            var ly: CGFloat = 0
            if let win = window {
                let ex = win.frame.minX + c.x
                let ey = win.frame.maxY - c.y
                let vx = m.x - ex
                let vy = ey - m.y                      // screen y points up, our view points down
                let dist = hypot(vx, vy)
                if dist > 1 {
                    let k = min(1, dist / 160)
                    lx = vx / dist * k
                    ly = vy / dist * k
                }
            }
            let s = cur[7] * appear
            let w0 = 64 * s
            let h0 = 62 * s
            let r = cur[8] * min(w0, h0) / 2
            let maxx = max(0, w0 / 2 - r - 5)
            let maxy = max(0, h0 / 2 - r - 5)
            let wt = 1 - 0.6 * cur[10]
            let sgn: CGFloat = (i == 0) ? -1 : 1
            let phase = Double(i) * 1.7
            var ox = lx * maxx * wt + sgn * cur[13] * maxx * 0.6
            var oy = ly * maxy * wt + cur[9] * maxy
            ox += CGFloat(sin(now * 0.9 + phase)) * cur[10] * maxx * 0.7
            oy += CGFloat(cos(now * 0.7 + phase)) * cur[10] * maxy * 0.5
            ox = max(-maxx, min(maxx, ox))
            oy = max(-maxy, min(maxy, oy))
            // spring (slightly underdamped), a little different per eye
            let k: CGFloat = (i == 0) ? 220 : 195
            let cdamp: CGFloat = 2 * sqrt(k) * 0.72
            vel[i].x += (k * (ox - pos[i].x) - cdamp * vel[i].x) * d
            vel[i].y += (k * (oy - pos[i].y) - cdamp * vel[i].y) * d
            pos[i].x += vel[i].x * d
            pos[i].y += vel[i].y * d
        }
    }

    func heartPath(_ cx: CGFloat, _ cy: CGFloat, _ s: CGFloat) -> CGPath {
        let p = CGMutablePath()
        for i in 0..<80 {
            let t = Double(i) / 80.0 * 2.0 * Double.pi
            let x = 16.0 * pow(sin(t), 3.0)
            let y = -(13.0 * cos(t) - 5.0 * cos(2.0 * t) - 2.0 * cos(3.0 * t) - cos(4.0 * t))
            let pt = CGPoint(x: cx + CGFloat(x) * s / 34, y: cy + CGFloat(y) * s / 34)
            if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
        }
        p.closeSubpath()
        return p
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.clear(bounds)
        ctx.setFillColor(NSColor(calibratedWhite: 0, alpha: 0.01).cgColor)   // keeps the whole area clickable
        ctx.fill(bounds)
        let alphaMul = max(0, min(1, (appear - 0.6) / 0.4))
        if alphaMul <= 0.001 { return }
        drawEye(ctx, 0, alphaMul)
        drawEye(ctx, 1, alphaMul)
    }

    func drawEye(_ ctx: CGContext, _ i: Int, _ alphaMul: CGFloat) {
        let left = (i == 0)
        let c = eyeCenter(i)
        let s = cur[7] * appear
        let w = 64 * s
        let h0 = 62 * s
        let h = max(2, h0 * (1 - blink * 0.9))
        let x0 = c.x - w / 2
        let y0 = c.y - h / 2
        let x1 = c.x + w / 2
        let y1 = c.y + h / 2
        let big: CGFloat = 400

        ctx.saveGState()
        ctx.setAlpha(alphaMul)
        let rad = min(min(w, h) * cur[12], min(w, h) / 2)
        ctx.addPath(CGPath(roundedRect: CGRect(x: x0, y: y0, width: w, height: h), cornerWidth: rad, cornerHeight: rad, transform: nil))
        ctx.clip()

        // upper lid: a line from the inner to the outer edge, everything above it is hidden
        let tin = left ? cur[0] : cur[2]
        let tout = left ? cur[1] : cur[3]
        let xin = left ? x1 : x0
        let xout = left ? x0 : x1
        let yin = y0 + tin * h
        let yout = y0 + tout * h
        let slope = (yout - yin) / (xout - xin)
        let a = x0 - 20
        let b = x1 + 20
        let lid = CGMutablePath()
        lid.move(to: CGPoint(x: a, y: yin + slope * (a - xin)))
        lid.addLine(to: CGPoint(x: b, y: yin + slope * (b - xin)))
        lid.addLine(to: CGPoint(x: b, y: y1 + 20))
        lid.addLine(to: CGPoint(x: a, y: y1 + 20))
        lid.closeSubpath()
        ctx.addPath(lid)
        ctx.clip()

        // lower lid
        let bot = left ? cur[4] : cur[5]
        if bot > 0.001 {
            ctx.clip(to: CGRect(x: -big, y: -big, width: big * 2 + 400, height: (y1 - bot * h) + big))
        }

        // smile bite (turns the eye into an arch)
        let ch = cur[6]
        if ch > 0.001 {
            let R = w * 0.62
            let cyc = (y1 + 2 + R) * (1 - ch) + (y0 + 0.36 * h + R) * ch
            let bite = CGMutablePath()
            bite.addRect(CGRect(x: -big, y: -big, width: big * 2 + 400, height: big * 2 + 400))
            bite.addEllipse(in: CGRect(x: c.x - R, y: cyc - R, width: 2 * R, height: 2 * R))
            ctx.addPath(bite)
            ctx.clip(using: .evenOdd)
        }

        ctx.setFillColor(NSColor.white.cgColor)
        ctx.fill(CGRect(x: x0 - 2, y: y0 - 2, width: w + 4, height: h + 4))

        // pupil
        let r = cur[8] * min(w, h0) / 2
        if cur[8] > 0.01 {
            let px = c.x + pos[i].x
            let py = c.y + pos[i].y
            let heart = max(0, min(1, cur[11]))
            if heart < 1 {
                ctx.setAlpha(alphaMul * (1 - heart))
                ctx.setFillColor(NSColor.black.cgColor)
                ctx.fillEllipse(in: CGRect(x: px - r, y: py - r, width: 2 * r, height: 2 * r))
            }
            if heart > 0.01 {
                ctx.setAlpha(alphaMul * heart)
                ctx.setFillColor(NSColor(calibratedRed: 0.91, green: 0.2, blue: 0.39, alpha: 1.0).cgColor)
                ctx.addPath(heartPath(px, py + 0.1 * r, r * 2.3))
                ctx.fillPath()
            }
        }
        ctx.restoreGState()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKUIDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var statusItem: NSStatusItem!
    var panel: Panel!
    var panelHost: NSView!
    var bubble: BubblePanel!
    var eyes: EyesView!
    var web: WKWebView!
    var currentExpr = ""
    var idleTimer: Timer?
    var dismissTimer: Timer?
    var needsLoad = true
    var pendingStart = false
    var hadActive = false
    var askedMic = false
    var hotKeyRef: EventHotKeyRef?
    var handlerRef: EventHandlerRef?
    var hotkeyOK = false

    // MARK: launch
    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()
        buildWebAndPanel()
        buildBubble()
        buildStatusItem()
        registerHotKey()
    }

    // MARK: UI setup
    func buildMainMenu() {
        // Needed so Cmd+C / Cmd+V / Cmd+A / Cmd+W work in the web view (the app has no Dock icon).
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Instinct Voice", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        let winItem = NSMenuItem()
        main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        NSApp.mainMenu = main
    }

    func buildWebAndPanel() {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = WKWebsiteDataStore.default()   // keeps the saved passcode between launches
        cfg.mediaTypesRequiringUserActionForPlayback = []
        cfg.userContentController.add(self, name: "st")
        cfg.userContentController.addUserScript(WKUserScript(source: kStateJS, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 390, height: 680), configuration: cfg)
        web.uiDelegate = self
        web.navigationDelegate = self

        panel = Panel(contentRect: NSRect(x: 0, y: 0, width: 390, height: 680),
                      styleMask: [.titled, .closable, .utilityWindow],
                      backing: .buffered, defer: false)
        panel.title = "Instinct"
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = NSColor(calibratedRed: 1.0, green: 0.85, blue: 0.29, alpha: 1.0)
        panel.delegate = self
        panelHost = NSView(frame: NSRect(x: 0, y: 0, width: 390, height: 680))
        panel.contentView = panelHost
    }

    func buildBubble() {
        eyes = EyesView(frame: NSRect(x: 0, y: 0, width: 160, height: 100))
        eyes.onTap = { [weak self] in self?.dismissBubble() }
        eyes.onMenu = { [weak self] in self?.showPanel() }
        bubble = BubblePanel(contentRect: NSRect(x: 0, y: 0, width: 160, height: 100),
                             styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: false)
        bubble.isOpaque = false
        bubble.backgroundColor = NSColor.clear
        bubble.hasShadow = false
        bubble.isFloatingPanel = true
        bubble.hidesOnDeactivate = false
        bubble.isReleasedWhenClosed = false
        bubble.level = .statusBar
        bubble.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        bubble.contentView = eyes
        setExpr("flat")
    }

    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            if #available(macOS 11.0, *), let img = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Instinct Voice") {
                img.isTemplate = true
                b.image = img
            } else {
                b.title = "IV"
            }
            b.target = self
            b.action = #selector(statusClicked(_:))
            b.sendAction(on: [.leftMouseUp, .rightMouseUp])
            b.toolTip = "Instinct Voice"
        }
    }

    func makeMenu() -> NSMenu {
        let m = NSMenu()
        let t = NSMenuItem(title: "Panggil / tutup (hotkey)", action: #selector(toggleFromMenu), keyEquivalent: "")
        t.target = self
        m.addItem(t)
        let o = NSMenuItem(title: "Buka panel penuh (passcode, setelan, riwayat)", action: #selector(openPanelFromMenu), keyEquivalent: "")
        o.target = self
        m.addItem(o)
        if !hotkeyOK {
            let w = NSMenuItem(title: "Hotkey gagal didaftarkan (bentrok?)", action: nil, keyEquivalent: "")
            w.isEnabled = false
            m.addItem(w)
        }
        m.addItem(NSMenuItem.separator())
        let r = NSMenuItem(title: "Muat ulang", action: #selector(reload), keyEquivalent: "")
        r.target = self
        m.addItem(r)
        let p = NSMenuItem(title: "Izin mikrofon di Pengaturan Sistem...", action: #selector(openMicSettings), keyEquivalent: "")
        p.target = self
        m.addItem(p)
        let x = NSMenuItem(title: "Reset passcode dan data", action: #selector(resetData), keyEquivalent: "")
        x.target = self
        m.addItem(x)
        m.addItem(NSMenuItem.separator())
        let q = NSMenuItem(title: "Keluar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        m.addItem(q)
        return m
    }

    // MARK: hotkey (Carbon RegisterEventHotKey: no Accessibility permission needed)
    func registerHotKey() {
        let ud = UserDefaults.standard
        let keyCode = (ud.object(forKey: "hotkeyKey") as? Int) ?? 49          // 49 = Space
        let mods = (ud.object(forKey: "hotkeyMods") as? Int) ?? optionKey     // Option
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let st1 = InstallEventHandler(GetApplicationEventTarget(), { (_, _, _) -> OSStatus in
            DispatchQueue.main.async {
                (NSApp.delegate as? AppDelegate)?.toggle()
            }
            return noErr
        }, 1, &spec, nil, &handlerRef)
        let hkID = EventHotKeyID(signature: OSType(0x49565A31), id: 1)
        let st2 = RegisterEventHotKey(UInt32(keyCode), UInt32(mods), hkID, GetApplicationEventTarget(), 0, &hotKeyRef)
        hotkeyOK = (st1 == noErr && st2 == noErr)
        if !hotkeyOK {
            statusItem.button?.toolTip = "Instinct Voice (hotkey gagal, klik ikon ini)"
        }
    }

    // MARK: actions
    @objc func statusClicked(_ sender: Any?) {
        let ev = NSApp.currentEvent
        if ev?.type == .rightMouseUp || (ev?.modifierFlags.contains(.control) ?? false) {
            statusItem.menu = makeMenu()
            statusItem.button?.performClick(nil)
            statusItem.menu = nil
        } else {
            toggle()
        }
    }
    @objc func toggleFromMenu() { toggle() }
    @objc func openPanelFromMenu() { showPanel() }
    @objc func reload() { needsLoad = false; web.load(URLRequest(url: kURL)) }
    @objc func openMicSettings() {
        if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(u)
        }
    }
    @objc func resetData() {
        let store = WKWebsiteDataStore.default()
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: Date(timeIntervalSince1970: 0)) { [weak self] in
            self?.reload()
        }
    }

    func toggle() {
        if bubble.isVisible {
            dismissBubble()
        } else if panel.isVisible {
            hidePanel()
        } else {
            showBubble()
        }
    }

    // MARK: web view placement (the same web view moves between the bubble and the panel)
    func attachWeb(to host: NSView, resize: Bool) {
        if web.superview === host { return }
        web.removeFromSuperview()
        web.frame = host.bounds
        web.autoresizingMask = resize ? [NSView.AutoresizingMask.width, NSView.AutoresizingMask.height] : []
        host.addSubview(web)
    }

    func cancelTimers() {
        idleTimer?.invalidate()
        idleTimer = nil
        dismissTimer?.invalidate()
        dismissTimer = nil
    }

    // MARK: bubble
    func showBubble() {
        cancelTimers()
        hadActive = false
        attachWeb(to: eyes.holder, resize: false)
        setExpr("flat")
        eyes.speaking = false
        positionBubble()
        bubble.orderFrontRegardless()      // does not steal focus from the app you are using
        eyes.start()
        requestMicOnce()
        if needsLoad {
            needsLoad = false
            pendingStart = true
            web.load(URLRequest(url: kURL))
        } else {
            startSession(retry: 3)
        }
    }

    func dismissBubble() {
        cancelTimers()
        pendingStart = false
        stopSession()
        eyes.speaking = false
        eyes.stop()
        bubble.orderOut(nil)
        hadActive = false
        scheduleUnload()
    }

    func startSession(retry: Int) {
        guard web.url?.host == kURL.host else { return }
        web.evaluateJavaScript(kStartJS) { [weak self] res, _ in
            guard let self = self else { return }
            let r = (res as? String) ?? ""
            if r == "nopc" {
                // No passcode saved yet: open the full panel so it can be typed once.
                self.showPanel()
            } else if r != "ok" && retry > 0 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.startSession(retry: retry - 1) }
            }
        }
    }

    func stopSession() {
        web.evaluateJavaScript(kStopJS, completionHandler: nil)
    }

    func positionBubble() {
        var origin = NSPoint(x: 100, y: 100)
        let screen = statusItem.button?.window?.screen ?? NSScreen.main
        if let vf = screen?.visibleFrame {
            origin = NSPoint(x: vf.maxX - 160 - 16, y: vf.maxY - 100 - 12)
        }
        bubble.setFrameOrigin(origin)
    }

    func setExpr(_ name: String) {
        if name == currentExpr { return }
        currentExpr = name
        eyes.setExpr(name)
    }

    // State coming from the page: idle / connecting / listening / thinking / speaking (+ error flag).
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? String else { return }
        let parts = body.components(separatedBy: "|")
        let state = parts.first ?? "idle"
        let err = parts.count > 1 && parts[1] == "1"
        applyState(state, err: err)
    }

    func applyState(_ state: String, err: Bool) {
        guard bubble.isVisible else { return }
        if err {
            setExpr("sedih")
            eyes.speaking = false
            scheduleDismiss(4.0)
            return
        }
        switch state {
        case "listening":
            setExpr("normal"); eyes.speaking = false
        case "thinking":
            setExpr("bingung"); eyes.speaking = false
        case "speaking":
            setExpr("senang"); eyes.speaking = true
        case "connecting":
            setExpr("serius"); eyes.speaking = false
        default:
            eyes.speaking = false
            if hadActive {
                setExpr("ngantuk")      // session ended by itself: go to sleep, then disappear
                scheduleDismiss(1.5)
            } else {
                setExpr("flat")
            }
        }
        if state != "idle" {
            hadActive = true
            dismissTimer?.invalidate()
            dismissTimer = nil
        }
    }

    func scheduleDismiss(_ seconds: TimeInterval) {
        dismissTimer?.invalidate()
        dismissTimer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            self?.dismissBubble()
        }
    }

    // MARK: full panel
    func showPanel() {
        cancelTimers()
        if bubble.isVisible {
            eyes.speaking = false
            eyes.stop()
            bubble.orderOut(nil)      // the session (if running) keeps going inside the panel
        }
        attachWeb(to: panelHost, resize: true)
        if needsLoad {
            needsLoad = false
            web.load(URLRequest(url: kURL))
        }
        positionPanel()
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        requestMicOnce()
    }

    func hidePanel() {
        stopSession()
        panel.orderOut(nil)
        scheduleUnload()
    }

    // After 2 minutes with nothing on screen, drop the page to give its memory back.
    func scheduleUnload() {
        idleTimer?.invalidate()
        idleTimer = Timer.scheduledTimer(withTimeInterval: 120, repeats: false) { [weak self] _ in
            guard let self = self, !self.panel.isVisible, !self.bubble.isVisible else { return }
            self.needsLoad = true
            self.web.loadHTMLString(kBlankHTML, baseURL: nil)
        }
    }

    func positionPanel() {
        let size = panel.frame.size
        var origin = NSPoint(x: 100, y: 100)
        let screen = statusItem.button?.window?.screen ?? NSScreen.main
        if let vf = screen?.visibleFrame {
            var x = vf.maxX - size.width - 12
            if let f = statusItem.button?.window?.frame { x = f.midX - size.width / 2 }
            x = max(vf.minX + 8, min(x, vf.maxX - size.width - 8))
            let y = max(vf.minY + 8, vf.maxY - size.height - 6)
            origin = NSPoint(x: x, y: y)
        }
        panel.setFrameOrigin(origin)
    }

    func requestMicOnce() {
        if askedMic { return }
        askedMic = true
        if AVCaptureDevice.authorizationStatus(for: .audio) == .notDetermined {
            AVCaptureDevice.requestAccess(for: .audio) { _ in }
        }
    }

    // MARK: NSWindowDelegate
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        hidePanel()
        return false
    }

    // MARK: WKUIDelegate
    @available(macOS 12.0, *)
    func webView(_ webView: WKWebView,
                 requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        // Only our own server may use the microphone. The OS still shows its own one-time prompt.
        if origin.host == kURL.host && type == .microphone {
            decisionHandler(.grant)
        } else {
            decisionHandler(.deny)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.addButton(withTitle: "OK")
        a.addButton(withTitle: "Batal")
        completionHandler(a.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert()
        a.messageText = message
        a.runModal()
        completionHandler()
    }

    // MARK: WKNavigationDelegate
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if pendingStart && webView.url?.host == kURL.host {
            pendingStart = false
            startSession(retry: 3)
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        showOffline()
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        showOffline()
    }
    func showOffline() {
        needsLoad = true   // next open retries
        pendingStart = false
        web.loadHTMLString("<html><body style='font:16px -apple-system;background:#ffd84a;color:#14103a;padding:30px'>Tidak bisa konek ke server. Cek internet, lalu tutup dan buka lagi.</body></html>", baseURL: nil)
        if bubble.isVisible {
            setExpr("sedih")
            scheduleDismiss(3.0)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
SWIFT_EOF
cat > "$TMP/Info.plist" <<'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>InstinctVoice</string>
  <key>CFBundleIdentifier</key><string>id.reza.voicemenu</string>
  <key>CFBundleName</key><string>Instinct Voice</string>
  <key>CFBundleDisplayName</key><string>Instinct Voice</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>3.0</string>
  <key>CFBundleVersion</key><string>3</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSMicrophoneUsageDescription</key><string>Instinct Voice butuh mikrofon supaya kamu bisa ngobrol dengan asisten suara.</string>
</dict>
</plist>
PLIST_EOF
echo "Compile (${ARCH})..."
swiftc -O -target "${ARCH}-apple-macos12.0" -o "$TMP/InstinctVoice" "$TMP/main.swift"
pkill -x InstinctVoice 2>/dev/null || true
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$TMP/InstinctVoice" "$APP/Contents/MacOS/InstinctVoice"
cp "$TMP/Info.plist" "$APP/Contents/Info.plist"
xattr -cr "$APP" 2>/dev/null || true
codesign --force --sign - --identifier id.reza.voicemenu "$APP"
if [ "$LOGIN" = "1" ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<LA_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>id.reza.voicemenu</string>
<key>ProgramArguments</key><array><string>$APP/Contents/MacOS/InstinctVoice</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
LA_EOF
  echo "Auto-start saat login: aktif."
fi
open "$APP"
echo "Selesai. Ikon gelombang muncul di menu bar. Hotkey: Option+Spasi memanggil sepasang mata yang langsung mendengarkan. Klik mata = tutup; klik kanan / tahan = panel penuh (isi passcode sekali di sini)."
