// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
const { Clutter, GLib } = imports.gi;
const Main = imports.ui.main;
const SwitcherPopup = imports.ui.switcherPopup;
const Keyboard = imports.ui.status.keyboard;

const CONFIG_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'fast-layout-osd.json']);

function getTimeout() {
    try {
        if (GLib.file_test(CONFIG_FILE, GLib.FileTest.EXISTS)) {
            let [ok, contents] = GLib.file_get_contents(CONFIG_FILE);
            if (ok) {
                let str = imports.byteArray ? imports.byteArray.toString(contents) : contents.toString();
                let data = JSON.parse(str);
                if (data && typeof data.timeout === 'number')
                    return data.timeout;
            }
        }
    } catch (e) {
        // fallback
    }
    return 185; // 50% от 375 мс (185 мс)
}

let _origResetNoModsTimeout = null;
let _origKeyboardResetNoModsTimeout = null;
let _scrollSignalId = 0;
let _lastScrollTime = 0;

function _onScrollEvent(actor, event) {
    if (event.is_pointer_emulated && event.is_pointer_emulated())
        return Clutter.EVENT_PROPAGATE;

    let direction = event.get_scroll_direction();
    let isDown = false;
    let isUp = false;

    if (direction === Clutter.ScrollDirection.DOWN) {
        isDown = true;
    } else if (direction === Clutter.ScrollDirection.UP) {
        isUp = true;
    } else if (direction === Clutter.ScrollDirection.SMOOTH) {
        let [, dy] = event.get_scroll_delta();
        if (dy > 0.1)
            isDown = true;
        else if (dy < -0.1)
            isUp = true;
    }

    if (!isDown && !isUp)
        return Clutter.EVENT_PROPAGATE;

    let now = GLib.get_monotonic_time();
    if (now - _lastScrollTime < 150 * 1000)
        return Clutter.EVENT_STOP;
    _lastScrollTime = now;

    let indicator = Main.panel.statusArea.keyboard;
    let manager = (indicator && indicator._inputSourceManager) ||
                  (Keyboard.getInputSourceManager && Keyboard.getInputSourceManager());
    if (!manager)
        return Clutter.EVENT_PROPAGATE;

    let rawSources = manager.inputSources || manager._inputSources;
    if (!rawSources)
        return Clutter.EVENT_PROPAGATE;

    let sources = Object.values(rawSources);
    if (sources.length < 2)
        return Clutter.EVENT_PROPAGATE;

    let current = manager.currentSource || manager._currentSource;
    let idx = sources.findIndex(s => current && (s === current || s.id === current.id || s.index === current.index));
    if (idx === -1)
        idx = 0;

    let nextIdx = isDown
        ? (idx + 1) % sources.length
        : (idx - 1 + sources.length) % sources.length;

    sources[nextIdx].activate(true);
    return Clutter.EVENT_STOP;
}

function init() {}

function enable() {
    function fastReset() {
        if (this._noModsTimeoutId !== 0)
            GLib.source_remove(this._noModsTimeoutId);

        let timeout = getTimeout();
        this._noModsTimeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            timeout,
            () => {
                this._finish(global.display.get_current_time_roundtrip());
                this._noModsTimeoutId = 0;
                return GLib.SOURCE_REMOVE;
            });
    }

    _origResetNoModsTimeout = SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout;
    SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout = fastReset;

    if (Keyboard && Keyboard.InputSourcePopup) {
        _origKeyboardResetNoModsTimeout = Keyboard.InputSourcePopup.prototype._resetNoModsTimeout;
        Keyboard.InputSourcePopup.prototype._resetNoModsTimeout = fastReset;
    }

    let indicator = Main.panel.statusArea.keyboard;
    if (indicator) {
        indicator.reactive = true;
        _scrollSignalId = indicator.connect('scroll-event', _onScrollEvent);
    }
}

function disable() {
    if (_origResetNoModsTimeout) {
        SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout = _origResetNoModsTimeout;
        _origResetNoModsTimeout = null;
    }
    if (_origKeyboardResetNoModsTimeout && Keyboard && Keyboard.InputSourcePopup) {
        Keyboard.InputSourcePopup.prototype._resetNoModsTimeout = _origKeyboardResetNoModsTimeout;
        _origKeyboardResetNoModsTimeout = null;
    }

    if (_scrollSignalId !== 0) {
        let indicator = Main.panel.statusArea.keyboard;
        if (indicator)
            indicator.disconnect(_scrollSignalId);
        _scrollSignalId = 0;
    }
}
