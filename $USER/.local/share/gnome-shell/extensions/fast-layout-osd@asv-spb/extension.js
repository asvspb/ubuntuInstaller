// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
const { GLib } = imports.gi;
const SwitcherPopup = imports.ui.switcherPopup;
const Keyboard = imports.ui.status.keyboard;

let _origResetNoModsTimeout = null;
let _origKeyboardResetNoModsTimeout = null;

function init() {
    log('[FastLayoutOSD] init');
}

function enable() {
    log('[FastLayoutOSD] enable start');
    
    function fastReset() {
        log('[FastLayoutOSD] _resetNoModsTimeout executed!');
        if (this._noModsTimeoutId !== 0)
            GLib.source_remove(this._noModsTimeoutId);

        this._noModsTimeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            375,
            () => {
                log('[FastLayoutOSD] timeout fired, finishing popup');
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
        log('[FastLayoutOSD] patched Keyboard.InputSourcePopup as well');
    }

    log('[FastLayoutOSD] enable complete');
}

function disable() {
    log('[FastLayoutOSD] disable');
    if (_origResetNoModsTimeout) {
        SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout = _origResetNoModsTimeout;
        _origResetNoModsTimeout = null;
    }
    if (_origKeyboardResetNoModsTimeout && Keyboard && Keyboard.InputSourcePopup) {
        Keyboard.InputSourcePopup.prototype._resetNoModsTimeout = _origKeyboardResetNoModsTimeout;
        _origKeyboardResetNoModsTimeout = null;
    }
}
