(function () {
    "use strict";

    const OVERLAY_Z_INDEX = 2147483647;
    const LABEL_FONT = "600 11px sans-serif";
    const LABEL_PADDING = "2px 6px";
    const LABEL_BORDER_RADIUS = "3px";
    const COMPACT_THRESHOLD = 8;
    const COMPACT_FONT = "600 9px sans-serif";
    const INSET_STEP = 2;

    /* ── HighlightEngine ── */
    var HighlightEngine = {
        _elementMap: null,

        render: function (fields) {
            this._elementMap = this._elementMap || new Map();
            if (!fields || !fields.length) return;

            for (var fi = 0; fi < fields.length; fi++) {
                var field = fields[fi];
                var color = field.color || "#FF6B6B";
                var selectors = [field.selector].concat(
                    (field.fallback || []).map(function (fb) { return fb.selector || fb; })
                ).filter(Boolean);

                var matched = false;
                var activeSelector = null;
                var fbActivated = false;
                var fbCount = 0;

                for (var si = 0; si < selectors.length; si++) {
                    var els;
                    try {
                        els = document.querySelectorAll(selectors[si]);
                    } catch (e) {
                        continue;
                    }
                    if (els.length > 0) {
                        matched = true;
                        activeSelector = selectors[si];
                        if (si > 0) { fbActivated = true; fbCount = si; }

                        for (var ei = 0; ei < els.length; ei++) {
                            var el = els[ei];
                            var rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) continue;

                            var entries = this._elementMap.get(el);
                            if (!entries) {
                                entries = [];
                                this._elementMap.set(el, entries);
                                var cur = el.style.boxShadow || "";
                                el.__dc_original_boxShadow = cur === "" ? "none" : cur;
                            }
                            entries.push({ color: color, fieldIdx: fi });
                        }
                        break;
                    }
                }

                field._matched = matched;
                field._activeSelector = activeSelector || null;
                field._fbActivated = fbActivated;
                field._fbCount = fbCount;
            }

            /* Phase 2: apply composed box-shadow to all tracked elements */
            var els = this._elementMap;
            els.forEach(function (entries, el) {
                var original = el.__dc_original_boxShadow || "none";
                var insetParts = [];
                for (var i = 0; i < entries.length; i++) {
                    insetParts.push(
                        "inset 0 0 0 " + (INSET_STEP + i * INSET_STEP) + "px " + entries[i].color
                    );
                }
                var parts = (original === "" || original === "none")
                    ? insetParts
                    : [original].concat(insetParts);
                el.style.setProperty("box-shadow", parts.join(", "), "important");
            });
        },

        destroy: function () {
            if (!this._elementMap) return;
            this._elementMap.forEach(function (_entries, el) {
                var original = el.__dc_original_boxShadow;
                if (original === "" || original === "none" || original === undefined) {
                    el.style.removeProperty("box-shadow");
                } else {
                    el.style.setProperty("box-shadow", original, "important");
                }
                delete el.__dc_original_boxShadow;
            });
            this._elementMap.clear();
            this._elementMap = null;
        },

        rerender: function (fields) {
            this.destroy();
            this.render(fields);
        },
    };

    /* ── LabelManager ── */
    var LabelManager = {
        _root: null,
        _labels: null,

        _ensureRoot: function () {
            if (this._root && this._root.isConnected) return;
            this._removeRoot();
            var root = document.createElement("div");
            root.id = "__dc_preview_root";
            root.style.cssText = [
                "position:relative",
                "pointer-events:none",
                "z-index:" + OVERLAY_Z_INDEX,
            ].join(";");
            document.body.insertBefore(root, document.body.firstChild);
            this._root = root;
        },

        _getRootPageOffset: function () {
            if (!this._root) return { x: 0, y: 0 };
            var r = this._root.getBoundingClientRect();
            return { x: r.left + window.scrollX, y: r.top + window.scrollY };
        },

        _createLabelEl: function (color, text, compact) {
            var label = document.createElement("div");
            label.style.cssText = [
                "position:absolute",
                "pointer-events:none",
                "font:" + (compact ? COMPACT_FONT : LABEL_FONT),
                "padding:" + LABEL_PADDING,
                "border-radius:" + LABEL_BORDER_RADIUS,
                "color:#fff",
                "background:" + color,
                "box-shadow:0 1px 3px rgba(0,0,0,0.3)",
                "white-space:nowrap",
                "max-width:200px",
                "overflow:hidden",
                "text-overflow:ellipsis",
            ].join(";");
            label.textContent = text;
            return label;
        },

        render: function (fields) {
            this.destroy();
            this._labels = [];
            if (!fields || !fields.length) return;

            this._ensureRoot();
            var offset = this._getRootPageOffset();

            for (var fi = 0; fi < fields.length; fi++) {
                var field = fields[fi];
                var color = field.color || "#FF6B6B";
                var selectors = [field.selector].concat(
                    (field.fallback || []).map(function (fb) { return fb.selector || fb; })
                ).filter(Boolean);

                for (var si = 0; si < selectors.length; si++) {
                    var els;
                    try {
                        els = document.querySelectorAll(selectors[si]);
                    } catch (e) {
                        continue;
                    }
                    if (els.length > 0) {
                        var total = els.length;
                        for (var ei = 0; ei < total; ei++) {
                            var el = els[ei];
                            var rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) continue;

                            var compact = total > COMPACT_THRESHOLD;
                            var labelText = field.multiple
                                ? (field.name + " [" + (ei + 1) + "/" + total + "]")
                                : field.name;
                            var labelEl = this._createLabelEl(
                                color,
                                compact ? field.name : labelText,
                                compact
                            );

                            var pos = LabelPositioner.positionInPage(rect, labelEl, offset);
                            labelEl.style.left = pos.left + "px";
                            labelEl.style.top = pos.top + "px";

                            this._root.appendChild(labelEl);
                            this._labels.push({
                                el: labelEl,
                                targetEl: el,
                                field: field,
                            });
                        }
                        break;
                    }
                }
            }
        },

        reposition: function () {
            if (!this._labels || !this._labels.length) return;
            if (!this._root || !this._root.isConnected) {
                this.render(_activeFields);
                return;
            }
            var offset = this._getRootPageOffset();

            /* Phase 1: READ */
            var updates = [];
            for (var i = 0; i < this._labels.length; i++) {
                var label = this._labels[i];
                if (!label.targetEl.isConnected) {
                    updates.push({ el: label.el, hide: true });
                    continue;
                }
                var rect = label.targetEl.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) {
                    updates.push({ el: label.el, hide: true });
                    continue;
                }
                var pos = LabelPositioner.positionInPage(rect, label.el, offset);
                updates.push({ el: label.el, left: pos.left, top: pos.top });
            }

            /* Phase 2: WRITE */
            for (var j = 0; j < updates.length; j++) {
                var u = updates[j];
                if (u.hide) {
                    u.el.style.display = "none";
                } else {
                    u.el.style.display = "";
                    u.el.style.left = u.left + "px";
                    u.el.style.top = u.top + "px";
                }
            }
        },

        destroy: function () {
            if (this._labels) {
                for (var i = 0; i < this._labels.length; i++) {
                    var el = this._labels[i].el;
                    if (el.parentNode) el.parentNode.removeChild(el);
                }
                this._labels = null;
            }
            this._removeRoot();
        },

        _removeRoot: function () {
            if (this._root && this._root.parentNode) {
                this._root.parentNode.removeChild(this._root);
            }
            this._root = null;
        },
    };

    /* ── LabelPositioner ── */
    var LabelPositioner = {
        PADDING: 4,
        MARGIN: 2,

        positionInPage: function (rect, labelEl, offset) {
            var vw = window.innerWidth;
            var vh = window.innerHeight;
            var lw = labelEl.offsetWidth || 80;
            var lh = labelEl.offsetHeight || 18;

            var left = rect.left;
            var top = rect.top - lh - this.MARGIN;

            if (top < this.PADDING) {
                top = rect.top + rect.height + this.MARGIN;
            }
            if (top + lh > vh - this.PADDING) {
                top = rect.top - lh - this.MARGIN;
            }

            if (left < this.PADDING) {
                left = this.PADDING;
            }
            if (left + lw > vw - this.PADDING) {
                left = vw - lw - this.PADDING;
            }

            return {
                left: left + window.scrollX - offset.x,
                top: top + window.scrollY - offset.y,
            };
        },
    };

    /* ── SceneObserver ── */
    var SceneObserver = {
        _rafId: null,
        _mutationObserver: null,
        _resizeObserver: null,
        _onMutation: null,
        _onReposition: null,
        _handler: null,
        _scrollTargets: [],

        start: function (onMutation, onReposition) {
            this.stop();
            this._onMutation = onMutation;
            this._onReposition = onReposition;
            var self = this;

            /* scroll listeners on all scrollable ancestors + window */
            this._scrollTargets = [];
            var el = document.body;
            while (el && el !== document.documentElement) {
                var style = window.getComputedStyle(el);
                var overflow = style.overflow + style.overflowY;
                if (/(auto|scroll)/.test(overflow)) {
                    this._scrollTargets.push(el);
                }
                el = el.parentElement;
            }
            this._scrollTargets.push(window);

            for (var i = 0; i < this._scrollTargets.length; i++) {
                this._scrollTargets[i].addEventListener("scroll", this._onScrollEvent, { passive: true });
            }
            window.addEventListener("resize", this._onResizeEvent, { passive: true });

            if (typeof MutationObserver !== "undefined") {
                this._mutationObserver = new MutationObserver(function () {
                    self._rafDebounce("mutation");
                });
                this._mutationObserver.observe(document.body, {
                    childList: true,
                    subtree: true,
                });
            }

            if (typeof ResizeObserver !== "undefined") {
                this._resizeObserver = new ResizeObserver(function () {
                    self._rafDebounce("reposition");
                });
                this._resizeObserver.observe(document.body);
            }
        },

        _onScrollEvent: function () {
            SceneObserver._rafDebounce("reposition");
        },

        _onResizeEvent: function () {
            SceneObserver._rafDebounce("reposition");
        },

        _rafDebounce: function (kind) {
            if (this._rafId !== null) return;
            var self = this;
            this._rafId = requestAnimationFrame(function () {
                self._rafId = null;
                if (kind === "mutation" && self._onMutation) {
                    self._onMutation();
                } else if (self._onReposition) {
                    self._onReposition();
                }
            });
        },

        stop: function () {
            if (this._rafId !== null) {
                cancelAnimationFrame(this._rafId);
                this._rafId = null;
            }
            for (var i = 0; i < this._scrollTargets.length; i++) {
                this._scrollTargets[i].removeEventListener("scroll", this._onScrollEvent, { passive: true });
            }
            this._scrollTargets.length = 0;
            window.removeEventListener("resize", this._onResizeEvent, { passive: true });

            if (this._mutationObserver) {
                this._mutationObserver.disconnect();
                this._mutationObserver = null;
            }
            if (this._resizeObserver) {
                this._resizeObserver.disconnect();
                this._resizeObserver = null;
            }
            this._onMutation = null;
            this._onReposition = null;
        },
    };

    /* ── ExecutionReporter ── */
    var ExecutionReporter = {
        summarize: function (fields) {
            var totalFields = fields.length;
            var matched = 0;
            var unmatched = 0;
            var fbActivated = false;
            var mainActive = 0;
            var fbCount = 0;

            for (var i = 0; i < fields.length; i++) {
                var f = fields[i];
                if (f._matched) {
                    matched++;
                    if (f._fbActivated) {
                        fbActivated = true;
                        fbCount = Math.max(fbCount, f._fbCount);
                    } else {
                        mainActive++;
                    }
                } else {
                    unmatched++;
                }
            }

            return {
                total: totalFields,
                matched: matched,
                unmatched: unmatched,
                fallback_activated: fbActivated,
                main_active: mainActive,
                fallback_count: fbCount,
            };
        },
    };

    /* ── Entry ── */
    var _activeFields = null;
    var _destroyed = false;

    function _onMutation() {
        if (_destroyed || !_activeFields) return;
        HighlightEngine.rerender(_activeFields);
        LabelManager.render(_activeFields);
    }

    function _onReposition() {
        if (_destroyed || !_activeFields) return;
        LabelManager.reposition();
    }

    window.__astrocrawl_preview = function (params) {
        _destroyed = false;
        _activeFields = (params && params.fields) ? params.fields.slice() : [];

        for (var i = 0; i < _activeFields.length; i++) {
            _activeFields[i]._matched = false;
            _activeFields[i]._activeSelector = null;
            _activeFields[i]._fbActivated = false;
            _activeFields[i]._fbCount = 0;
        }

        HighlightEngine.destroy();
        HighlightEngine.render(_activeFields);
        LabelManager.render(_activeFields);
        SceneObserver.start(_onMutation, _onReposition);

        return ExecutionReporter.summarize(_activeFields);
    };

    window.__astrocrawl_destroy = function () {
        _destroyed = true;
        _activeFields = null;
        SceneObserver.stop();
        HighlightEngine.destroy();
        LabelManager.destroy();
    };

    window.__astrocrawl_update_theme = function (_themeParams) {
        /* reserved: theme tokens available for future label color adaptation */
    };

    /* AI micro-tuning hook (reserved) */
    window.__astrocrawl_select = null;
})();
