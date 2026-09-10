// -*- mode: js; js-indent-level: 4; indent-tabs-mode: nil -*-
const { GLib } = imports.gi;
const SwitcherPopup = imports.ui.switcherPopup;

let _origResetNoModsTimeout = null;

function init() {}

function enable() {
    _origResetNoModsTimeout = SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout;
    SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout = function() {
        if (this._noModsTimeoutId !== 0)
            GLib.source_remove(this._noModsTimeoutId);

        // Время показа уменьшено на 75% (с 1500 мс до 375 мс)
        this._noModsTimeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT,
            375,
            () => {
                this._finish(global.display.get_current_time_roundtrip());
                this._noModsTimeoutId = 0;
                return GLib.SOURCE_REMOVE;
            });
    };
}

function disable() {
    if (_origResetNoModsTimeout) {
        SwitcherPopup.SwitcherPopup.prototype._resetNoModsTimeout = _origResetNoModsTimeout;
        _origResetNoModsTimeout = null;
    }
}
