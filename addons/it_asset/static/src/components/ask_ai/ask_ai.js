/** @odoo-module **/

// Ask AI thin client — cerminan pemisahan WACS: "Qwen = language
// understanding, Go = business logic". Di sini: JS hanya merender,
// SEMUA keputusan + query data terjadi di backend Python
// (models/ask_ai.py + models/ask_ai_nlu.py). JS tidak boleh mengarang fakta.

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, useRef, useState } from "@odoo/owl";

export class ITAskAI extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.chatBodyRef = useRef("chatBody");
        this.inputRef = useRef("chatInput");

        this.state = useState({
            input: "",
            isTyping: false,
            activeSessionId: 1,
            search: "",
            showSidebar: false,
            sessions: [
                { id: 1, title: "Rekap aset & stok hari ini", prompt: "Rekap jumlah aset saat ini", date: "Hari ini", active: true },
                { id: 2, title: "Stok consumable menipis", prompt: "Stok consumable apa saja yang menipis?", date: "Hari ini", active: false },
                { id: 3, title: "Aset rusak / broken", prompt: "Tampilkan aset yang kondisinya broken", date: "Kemarin", active: false },
            ],
            messages: [
                {
                    id: 1,
                    role: "ai",
                    content:
                        "Halo! Saya <b>GSI IT Assistant</b> 🤖<br/>" +
                        "Saya membaca <b>data live modul IT</b> — <b>stok produk, jumlah aset, pengguna, kondisi, dan riwayat</b>.<br/><br/>" +
                        "Contoh: <i>“stok toner tersisa berapa?”</i> • <i>“rekap aset”</i> • <i>“siapa yang pakai LT-012?”</i> • <i>“riwayat printer PRN-01”</i>",
                    time: this._now(),
                    action: null,
                },
            ],
            suggestions: [
                { icon: "fa-cubes", title: "Rekap Aset", desc: "Total, tersedia, dipakai, rusak", prompt: "Rekap jumlah aset saat ini" },
                { icon: "fa-archive", title: "Stok Menipis", desc: "Consumable di bawah minimum", prompt: "Stok consumable apa saja yang menipis?" },
                { icon: "fa-user", title: "Cek Pengguna", desc: "Siapa pemakai aset tertentu", prompt: "Siapa yang pakai aset LT-" },
                { icon: "fa-history", title: "Aset Broken", desc: "Kondisi broken + riwayat", prompt: "Tampilkan aset yang kondisinya broken" },
            ],
        });

        onMounted(() => {
            this._scrollToBottom(true);
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        });
    }

    // ---------- helpers (rendering saja, tanpa logika bisnis) ----------
    _now() {
        return new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
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
    get filteredSessions() {
        const q = (this.state.search || "").toLowerCase().trim();
        if (!q) {
            return this.state.sessions;
        }
        return this.state.sessions.filter((s) => s.title.toLowerCase().includes(q));
    }
    get canSend() {
        return this.state.input.trim().length > 0 && !this.state.isTyping;
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
        this.state.messages.push({
            id: Date.now(),
            role: "user",
            content: this._escapeHtml(text).replace(/\n/g, "<br/>"),
            time: this._now(),
            action: null,
        });
        this.state.input = "";
        if (this.inputRef.el) {
            this.inputRef.el.style.height = "auto";
        }
        this._updateSessionTitle(text);
        this._scrollToBottom();

        // Satu-satunya sumber jawaban: backend. Gagal = pesan error jujur,
        // bukan karangan (cermin "data tidak boleh dikarang" WACS).
        this.state.isTyping = true;
        this._scrollToBottom();
        try {
            const res = await this.orm.call("it_asset.ask_ai", "answer", [text]);
            this.state.messages.push({
                id: Date.now() + 1,
                role: "ai",
                content: (res && res.html) || "Maaf, backend tidak mengembalikan jawaban.",
                time: this._now(),
                action: (res && res.action) || null,
            });
        } catch (e) {
            this.state.messages.push({
                id: Date.now() + 1,
                role: "ai",
                content:
                    "Maaf, saya tidak dapat menghubungi backend Ask AI" +
                    (e && e.message ? ": <i>" + this._escapeHtml(e.message) + "</i>" : ".") +
                    "<br/>Coba kirim ulang, atau upgrade modul <b>IT Department</b> bila ini instalasi baru.",
                time: this._now(),
                action: null,
            });
        }
        this.state.isTyping = false;
        this._scrollToBottom();
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
    newChat() {
        const id = Date.now();
        this.state.sessions.forEach((s) => (s.active = false));
        this.state.sessions.unshift({ id, title: "Percakapan baru", prompt: "", date: "Hari ini", active: true });
        this.state.activeSessionId = id;
        this.state.messages = [
            {
                id: Date.now(),
                role: "ai",
                content: "Percakapan baru dimulai ✨<br/>Tanya langsung dari data live, mis: <i>“rekap aset”</i>, <i>“stok mouse berapa?”</i>, <i>“aset rusak apa saja?”</i>",
                time: this._now(),
                action: null,
            },
        ];
        this.state.showSidebar = false;
        this._scrollToBottom(true);
        if (this.inputRef.el) {
            this.inputRef.el.focus();
        }
    }
    selectSession(session) {
        this.state.sessions.forEach((s) => (s.active = s.id === session.id));
        this.state.activeSessionId = session.id;
        this.state.showSidebar = false;
        if (session.prompt && !this.state.isTyping) {
            this.state.input = session.prompt;
            this.sendMessage();
        } else {
            this._scrollToBottom(true);
        }
    }
    clearChat() {
        this.state.messages = [
            { id: Date.now(), role: "ai", content: "Riwayat chat dibersihkan 🧹<br/>Mau cek data apa lagi?", time: this._now(), action: null },
        ];
        this._scrollToBottom();
    }
    toggleSidebar() {
        this.state.showSidebar = !this.state.showSidebar;
    }
    _updateSessionTitle(text) {
        const active = this.state.sessions.find((s) => s.active);
        if (active && (active.title === "Percakapan baru" || !active.title)) {
            active.title = text.length > 38 ? text.slice(0, 38) + "…" : text;
        }
    }
}

ITAskAI.template = "it_asset.AskAI";
registry.category("actions").add("it_asset_ask_ai_action", ITAskAI);
