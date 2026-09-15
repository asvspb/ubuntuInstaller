// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
const { GLib } = imports.gi;
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
}
