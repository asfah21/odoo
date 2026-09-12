/** @odoo-module **/

// Ask AI thin client — JS hanya merender. SEMUA keputusan + query data di
// backend Python (models/ask_ai.py + ask_ai_nlu.py). Riwayat tersimpan di
// backend (it_asset.ask_ai.session) dan terhapus otomatis setelah 10 hari
// tidak aktif via cron harian.

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onPatched, onWillStart, useRef, useState } from "@odoo/owl";

const WELCOME_HTML =
    "Halo! Saya <b>GSI IT Assistant</b><br/>" +
    "Tanya langsung dari data live: <i>“rekap aset”</i> • <i>“stok toner?”</i> • " +
    "<i>“siapa yang pakai LT-012?”</i>";

export class ITAskAI extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.chatBodyRef = useRef("chatBody");
        this.inputRef = useRef("chatInput");

        this.state = useState({
            input: "",
            isTyping: false,
            loadingHistory: true,
            activeSessionId: null,
            sessions: [],
            messages: [],
            suggestions: [
                { icon: "fa-cubes", label: "Rekap aset", prompt: "Rekap jumlah aset saat ini" },
                { icon: "fa-archive", label: "Stok menipis", prompt: "Stok consumable apa saja yang menipis?" },
                { icon: "fa-user", label: "Cek pengguna LT-012", prompt: "Siapa yang pakai LT-012?" },
            ],
        });

        onWillStart(async () => {
            await this.loadSessions();
        });
        onMounted(() => {
            this._renderBubbles();
            this._scrollToBottom(true);
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        });
        // innerHTML langsung (bukan t-out) agar HTML backend tampil
        // sebagai tabel/kartu, bukan teks mentah.
        onPatched(() => this._renderBubbles());
    }

    _renderBubbles() {
        const root = this.chatBodyRef.el;
        if (!root) {
            return;
        }
        const byId = new Map(
            this.state.messages.map((m) => [String(m.id), m.content || ""])
        );
        for (const el of root.querySelectorAll(".ask-bubble-text[data-mid]")) {
            const html = byId.get(el.dataset.mid);
            if (html !== undefined && el.__askHtml !== html) {
                el.innerHTML = html;
                el.__askHtml = html;
            }
        }
    }

    // ---------- helpers ----------
    _now() {
        return new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
    }
    _fmtDate(dt) {
        if (!dt) {
            return "";
        }
        const d = new Date(String(dt).replace(" ", "T") + "Z");
        if (isNaN(d)) {
            return String(dt).slice(0, 10);
        }
        const today = new Date();
        const sameDay = d.toDateString() === today.toDateString();
        const hm = d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
        return sameDay ? hm : d.toLocaleDateString("id-ID", { day: "numeric", month: "short" });
    }
    _scrollToBottom(instant = false) {
        requestAnimationFrame(() => {
            const el = this.chatBodyRef.el;
            if (el) {
                el.scrollTo({ top: el.scrollHeight, behavior: instant ? "auto" : "smooth" });
            }
        });
    }
    _autoGrow() {
        const el = this.inputRef.el;
        if (!el) {
            return;
        }
        el.style.height = "auto";
        el.style.height = Math.min(el.scrollHeight, 140) + "px";
    }
    _escapeHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }
    get canSend() {
        return this.state.input.trim().length > 0 && !this.state.isTyping;
    }
    get activeSession() {
        return this.state.sessions.find((s) => s.id === this.state.activeSessionId) || null;
    }

    // ---------- history (backend, retensi 10 hari) ----------
    async loadSessions() {
        this.state.loadingHistory = true;
        try {
            const rows = await this.orm.call("it_asset.ask_ai", "session_list", [], { limit: 30 });
            this.state.sessions = rows || [];
            if (this.state.sessions.length) {
                await this.selectSession(this.state.sessions[0]);
            } else {
                await this.newChat(false);
            }
        } catch (e) {
            this.state.sessions = [];
            this.state.messages = [{ id: "w" + Date.now(), role: "ai", content: WELCOME_HTML, time: this._now(), action: null }];
        }
        this.state.loadingHistory = false;
    }
    async selectSession(session) {
        if (!session || this.state.isTyping) {
            return;
        }
        this.state.activeSessionId = session.id;
        this.state.loadingHistory = true;
        try {
            const res = await this.orm.call("it_asset.ask_ai", "session_get", [session.id]);
            const msgs = (res && res.messages) || [];
            this.state.messages = msgs.map((m) => ({
                id: m.id,
                role: m.role,
                content: m.body_html,
                time: this._fmtDate(m.create_date),
                action: null,
            }));
            if (!this.state.messages.length) {
                this.state.messages = [{ id: "w" + Date.now(), role: "ai", content: WELCOME_HTML, time: this._now(), action: null }];
            }
        } catch (e) {
            this.state.messages = [{ id: "w" + Date.now(), role: "ai", content: WELCOME_HTML, time: this._now(), action: null }];
        }
        this.state.loadingHistory = false;
        this._scrollToBottom(true);
    }
    async newChat(reload = true) {
        try {
            const res = await this.orm.call("it_asset.ask_ai", "session_create", [], { name: "Percakapan baru" });
            if (res && res.id) {
                this.state.sessions.unshift({ id: res.id, name: res.name, last_seen: null, message_count: 0 });
                this.state.activeSessionId = res.id;
            }
        } catch (e) {
            this.state.activeSessionId = null;
        }
        this.state.messages = [{ id: "w" + Date.now(), role: "ai", content: WELCOME_HTML, time: this._now(), action: null }];
        this._scrollToBottom(true);
        if (reload && this.inputRef.el) {
            this.inputRef.el.focus();
        }
    }
    async deleteSession(session, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!session) {
            return;
        }
        try {
            await this.orm.call("it_asset.ask_ai", "session_delete", [session.id]);
        } catch (e) {
            return;
        }
        this.state.sessions = this.state.sessions.filter((s) => s.id !== session.id);
        if (this.state.activeSessionId === session.id) {
            if (this.state.sessions.length) {
                await this.selectSession(this.state.sessions[0]);
            } else {
                await this.newChat(false);
            }
        }
    }

    // ---------- events ----------
    onInput(ev) {
        this.state.input = ev.target.value;
        this._autoGrow();
    }
    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }
    useSuggestion(prompt) {
        this.state.input = prompt;
        this._autoGrow();
        if (this.inputRef.el) {
            this.inputRef.el.focus();
        }
        this.sendMessage();
    }
    async sendMessage() {
        const text = (this.state.input || "").trim();
        if (!text || this.state.isTyping) {
            return;
        }
        if (!this.state.activeSessionId) {
            await this.newChat(false);
        }
        this.state.messages.push({
            id: "u" + Date.now(),
            role: "user",
            content: this._escapeHtml(text).replace(/\n/g, "<br/>"),
            time: this._now(),
            action: null,
        });
        this.state.input = "";
        if (this.inputRef.el) {
            this.inputRef.el.style.height = "auto";
        }
        this._scrollToBottom();

        // Satu-satunya sumber jawaban: backend. Gagal = pesan error jujur.
        this.state.isTyping = true;
        this._scrollToBottom();
        try {
            const res = await this.orm.call("it_asset.ask_ai", "answer", [text, this.state.activeSessionId]);
            if (res && res.session_id && res.session_id !== this.state.activeSessionId) {
                this.state.activeSessionId = res.session_id;
            }
            this.state.messages.push({
                id: "a" + (Date.now() + 1),
                role: "ai",
                content: (res && res.html) || "Maaf, backend tidak mengembalikan jawaban.",
                time: this._now(),
                action: (res && res.action) || null,
            });
            this._refreshSessionRow(text);
        } catch (e) {
            this.state.messages.push({
                id: "a" + (Date.now() + 1),
                role: "ai",
                content:
                    "Maaf, saya tidak dapat menghubungi backend Ask AI" +
                    (e && e.message ? ": <i>" + this._escapeHtml(e.message) + "</i>" : "."),
                time: this._now(),
                action: null,
            });
        }
        this.state.isTyping = false;
        this._scrollToBottom();
    }
    _refreshSessionRow(text) {
        const s = this.state.sessions.find((x) => x.id === this.state.activeSessionId);
        if (s) {
            if (!s.name || s.name === "Percakapan baru") {
                s.name = text.length > 38 ? text.slice(0, 38) + "…" : text;
            }
            this.state.sessions.sort((a, b) => (a.id === this.state.activeSessionId ? -1 : b.id === this.state.activeSessionId ? 1 : 0));
        } else if (this.state.activeSessionId) {
            this.state.sessions.unshift({ id: this.state.activeSessionId, name: text.slice(0, 38), last_seen: null, message_count: 0 });
        }
    }
    openMsgAction(msg) {
        const a = msg && msg.action;
        if (!a || !a.res_model) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: a.name || "Data",
            res_model: a.res_model,
            views: [[false, "list"], [false, "form"]],
            domain: a.domain || [],
            target: "current",
        });
    }
}

ITAskAI.template = "it_asset.AskAI";
registry.category("actions").add("it_asset_ask_ai_action", ITAskAI);
