/** @odoo-module **/

// Ask AI thin client — cerminan pemisahan WACS: "Qwen = language
// understanding, Go = business logic". Di sini: JS hanya merender,
// SEMUA keputusan + query data terjadi di backend Python
// (models/ask_ai.py + models/ask_ai_nlu.py). JS tidak boleh mengarang fakta.
//
// Riwayat tersimpan di backend (it_asset.ask_ai.session) dan terhapus
// otomatis setelah 10 hari tidak aktif via cron harian.

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onPatched, onWillStart, useRef, useState } from "@odoo/owl";

const WELCOME_HTML =
    "Halo! Saya <b>GSI IT Assistant</b> 🤖<br/>" +
    "Saya membaca <b>data live modul IT</b> — <b>stok produk, jumlah aset, pengguna, kondisi, dan riwayat</b>.<br/><br/>" +
        "Contoh: <i>“stok radio ht tersisa berapa?”</i> • <i>“rekap aset”</i> • <i>“siapa yang pakai ITLT-007?”</i> • <i>“riwayat printer PRN-01”</i>";

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
            search: "",
            showSidebar: false,
            sessions: [],
            messages: [
                {
                    id: "w" + Date.now(),
                    role: "ai",
                    content: WELCOME_HTML,
                    time: this._now(),
                    action: null,
                },
            ],
            suggestions: [
                { icon: "fa-cubes", title: "Rekap Aset", desc: "Total, tersedia, dipakai, rusak", prompt: "Rekap jumlah aset saat ini" },
                { icon: "fa-archive", title: "Stok Menipis", desc: "Consumable di bawah minimum", prompt: "Stok consumable apa saja yang menipis?" },
                { icon: "fa-user", title: "Cek Pengguna", desc: "Siapa pemakai aset tertentu", prompt: "Siapa yang pakai aset ITLT-" },
                { icon: "fa-history", title: "Aset Broken", desc: "Kondisi broken + riwayat", prompt: "Tampilkan aset yang kondisinya broken" },
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
        // Render ulang isi bubble setiap ada pesan baru. Injeksi langsung via
        // innerHTML (bukan t-out) agar HTML jawaban backend SELALU tampil
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

    // ---------- helpers (rendering saja, tanpa logika bisnis) ----------
    _now() {
        return new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
    }
    _fmtHour(dt) {
        if (!dt) {
            return this._now();
        }
        const d = new Date(String(dt).replace(" ", "T") + "Z");
        if (isNaN(d)) {
            return this._now();
        }
        return d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
    }
    _fmtDay(dt) {
        if (!dt) {
            return "Hari ini";
        }
        const d = new Date(String(dt).replace(" ", "T") + "Z");
        if (isNaN(d)) {
            return "Hari ini";
        }
        const today = new Date();
        const yesterday = new Date();
        yesterday.setDate(today.getDate() - 1);
        if (d.toDateString() === today.toDateString()) {
            return "Hari ini";
        }
        if (d.toDateString() === yesterday.toDateString()) {
            return "Kemarin";
        }
        return d.toLocaleDateString("id-ID", { day: "numeric", month: "short" });
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
        return this.state.sessions.filter((s) => (s.title || "").toLowerCase().includes(q));
    }
    get canSend() {
        return this.state.input.trim().length > 0 && !this.state.isTyping;
    }

    // ---------- riwayat (backend, retensi 10 hari) ----------
    async loadSessions() {
        this.state.loadingHistory = true;
        try {
            const rows = await this.orm.call("it_asset.ask_ai", "session_list", [], { limit: 30 });
            this.state.sessions = (rows || []).map((r) => ({
                id: r.id,
                title: r.name || "Percakapan baru",
                date: this._fmtDay(r.last_seen),
                active: false,
            }));
            if (this.state.sessions.length) {
                await this.selectSession(this.state.sessions[0]);
            } else {
                await this.newChat();
            }
        } catch (e) {
            this.state.sessions = [];
        }
        this.state.loadingHistory = false;
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
            await this.newChat();
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
        this._updateSessionTitle(text);
        this._scrollToBottom();

        // Satu-satunya sumber jawaban: backend. Gagal = pesan error jujur,
        // bukan karangan (cermin "data tidak boleh dikarang" WACS).
        this.state.isTyping = true;
        this._scrollToBottom();
        try {
            const res = await this.orm.call("it_asset.ask_ai", "answer", [text, this.state.activeSessionId]);
            if (res && res.session_id && res.session_id !== this.state.activeSessionId) {
                this.state.activeSessionId = res.session_id;
                const row = this.state.sessions.find((s) => s.id === res.session_id);
                if (row) {
                    this.state.sessions.forEach((s) => (s.active = s.id === res.session_id));
                }
            }
            this.state.messages.push({
                id: "a" + (Date.now() + 1),
                role: "ai",
                content: (res && res.html) || "Maaf, backend tidak mengembalikan jawaban.",
                time: this._now(),
                action: (res && res.action) || null,
            });
            this._moveActiveFirst();
        } catch (e) {
            this.state.messages.push({
                id: "a" + (Date.now() + 1),
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
    async newChat() {
        try {
            const res = await this.orm.call("it_asset.ask_ai", "session_create", [], { name: "Percakapan baru" });
            this.state.sessions.forEach((s) => (s.active = false));
            if (res && res.id) {
                this.state.sessions.unshift({ id: res.id, title: res.name || "Percakapan baru", date: "Hari ini", active: true });
                this.state.activeSessionId = res.id;
            } else {
                this.state.activeSessionId = null;
            }
        } catch (e) {
            const id = "tmp" + Date.now();
            this.state.sessions.forEach((s) => (s.active = false));
            this.state.sessions.unshift({ id, title: "Percakapan baru", date: "Hari ini", active: true });
            this.state.activeSessionId = id;
        }
        this.state.messages = [
            {
                id: "w" + Date.now(),
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
    async selectSession(session) {
        if (!session || this.state.isTyping) {
            return;
        }
        this.state.sessions.forEach((s) => (s.active = s.id === session.id));
        this.state.activeSessionId = session.id;
        this.state.showSidebar = false;
        // Muat isi percakapan dari backend (bukan kirim ulang prompt).
        if (typeof session.id === "number") {
            this.state.loadingHistory = true;
            try {
                const res = await this.orm.call("it_asset.ask_ai", "session_get", [session.id]);
                const msgs = (res && res.messages) || [];
                this.state.messages = msgs.map((m) => ({
                    id: m.id,
                    role: m.role,
                    content: m.body_html,
                    time: this._fmtHour(m.create_date),
                    action: null,
                }));
                if (!this.state.messages.length) {
                    this.state.messages = [
                        {
                            id: "w" + Date.now(),
                            role: "ai",
                            content: "Percakapan baru dimulai ✨<br/>Tanya langsung dari data live, mis: <i>“rekap aset”</i>, <i>“stok mouse berapa?”</i>, <i>“aset rusak apa saja?”</i>",
                            time: this._now(),
                            action: null,
                        },
                    ];
                }
            } catch (e) {
                // Biarkan pesan yang ada; jangan kosongkan layar.
            }
            this.state.loadingHistory = false;
        }
        this._scrollToBottom(true);
    }
    async deleteSession(session, ev) {
        if (ev) {
            ev.stopPropagation();
        }
        if (!session) {
            return;
        }
        if (typeof session.id === "number") {
            try {
                await this.orm.call("it_asset.ask_ai", "session_delete", [session.id]);
            } catch (e) {
                return;
            }
        }
        const wasActive = session.id === this.state.activeSessionId;
        this.state.sessions = this.state.sessions.filter((s) => s.id !== session.id);
        if (wasActive) {
            if (this.state.sessions.length) {
                await this.selectSession(this.state.sessions[0]);
            } else {
                await this.newChat();
            }
        }
    }
    async clearChat() {
        // Hapus sesi aktif di backend lalu buka sesi baru yang bersih.
        const active = this.state.sessions.find((s) => s.active);
        if (active) {
            if (typeof active.id === "number") {
                try {
                    await this.orm.call("it_asset.ask_ai", "session_delete", [active.id]);
                } catch (e) {
                    // Lanjut: minimal tampilan dibersihkan.
                }
            }
            this.state.sessions = this.state.sessions.filter((s) => s.id !== active.id);
        }
        await this.newChat();
        this.state.messages = [
            { id: "c" + Date.now(), role: "ai", content: "Riwayat chat dibersihkan 🧹<br/>Mau cek data apa lagi?", time: this._now(), action: null },
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
    _moveActiveFirst() {
        const idx = this.state.sessions.findIndex((s) => s.id === this.state.activeSessionId);
        if (idx > 0) {
            const [row] = this.state.sessions.splice(idx, 1);
            row.date = "Hari ini";
            this.state.sessions.unshift(row);
        } else if (idx === 0) {
            this.state.sessions[0].date = "Hari ini";
        }
    }
}

ITAskAI.template = "it_asset.AskAI";
registry.category("actions").add("it_asset_ask_ai_action", ITAskAI);
