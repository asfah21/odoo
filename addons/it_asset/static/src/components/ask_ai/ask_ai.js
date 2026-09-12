/** @odoo-module **/

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
                { id: 1, title: "Troubleshooting printer Epson", date: "Hari ini", active: true },
                { id: 2, title: "Cara request laptop baru", date: "Hari ini", active: false },
                { id: 3, title: "Cek garansi ThinkPad X1", date: "Kemarin", active: false },
                { id: 4, title: "Prosedur damage report", date: "12 Agu", active: false },
            ],
            messages: [
                {
                    id: 1,
                    role: "ai",
                    content: "Halo! Saya <b>GSI IT Assistant</b> 🤖<br/>Saya bisa bantu seputar aset IT, request barang, troubleshooting, dan prosedur form IT di Site Wolo. Ada yang bisa saya bantu hari ini?",
                    time: this._now(),
                },
            ],
            suggestions: [
                {
                    icon: "fa-laptop",
                    title: "Request Aset",
                    desc: "Cara mengajukan laptop / aksesoris baru",
                    prompt: "Bagaimana cara mengajukan request laptop baru di modul IT?",
                },
                {
                    icon: "fa-wrench",
                    title: "Troubleshooting",
                    desc: "Printer, jaringan, atau PC bermasalah",
                    prompt: "Printer Epson tidak bisa print, bagaimana cara troubleshooting-nya?",
                },
                {
                    icon: "fa-file-text-o",
                    title: "Damage Report",
                    desc: "Lapor aset rusak / hilang",
                    prompt: "Bagaimana prosedur membuat Damage Report untuk aset rusak?",
                },
                {
                    icon: "fa-exchange",
                    title: "Handover Aset",
                    desc: "Serah terima ke karyawan / unit",
                    prompt: "Bagaimana alur Item Handover aset ke karyawan?",
                },
            ],
        });

        onMounted(() => {
            this._scrollToBottom(true);
            if (this.inputRef.el) {
                this.inputRef.el.focus();
            }
        });
    }

    // ---------- helpers ----------
    _now() {
        const d = new Date();
        return d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
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

    get lastTime() {
        if (!this.state.messages.length) {
            return "-";
        }
        return this.state.messages[this.state.messages.length - 1].time;
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
        // push user message
        this.state.messages.push({
            id: Date.now(),
            role: "user",
            content: this._escapeHtml(text).replace(/\n/g, "<br/>"),
            time: this._now(),
        });
        this.state.input = "";
        if (this.inputRef.el) {
            this.inputRef.el.style.height = "auto";
        }
        this._updateSessionTitle(text);
        this._scrollToBottom();

        // simulate AI thinking (frontend only)
        this.state.isTyping = true;
        this._scrollToBottom();
        await new Promise((r) => setTimeout(r, 1100 + Math.random() * 700));
        const reply = this._dummyReply(text);
        this.state.messages.push({
            id: Date.now() + 1,
            role: "ai",
            content: reply,
            time: this._now(),
        });
        this.state.isTyping = false;
        this._scrollToBottom();
    }

    newChat() {
        const id = Date.now();
        this.state.sessions.forEach((s) => (s.active = false));
        this.state.sessions.unshift({
            id,
            title: "Percakapan baru",
            date: "Hari ini",
            active: true,
        });
        this.state.activeSessionId = id;
        this.state.messages = [
            {
                id: Date.now(),
                role: "ai",
                content:
                    "Percakapan baru dimulai ✨<br/>Silakan tanyakan apa saja seputar <b>aset IT, inventory, form request, handover, atau troubleshooting</b>.",
                time: this._now(),
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
        // Frontend only: tampilkan salam konteks session (riwayat detail menyusul saat backend siap)
        if (session.id !== 1) {
            this.state.messages = [
                {
                    id: Date.now(),
                    role: "ai",
                    content: `Membuka riwayat <b>${this._escapeHtml(session.title)}</b>...<br/><br/>Mode demo front-end: riwayat lengkap akan tersambung ke backend nanti. Silakan lanjutkan bertanya di topik ini 👇`,
                    time: this._now(),
                },
            ];
        }
        this._scrollToBottom(true);
    }

    clearChat() {
        this.state.messages = [
            {
                id: Date.now(),
                role: "ai",
                content: "Riwayat chat dibersihkan 🧹<br/>Mau tanya apa lagi seputar IT?",
                time: this._now(),
            },
        ];
        this._scrollToBottom();
    }

    toggleSidebar() {
        this.state.showSidebar = !this.state.showSidebar;
    }

    // ---------- dummy brain (frontend only, ganti dengan RPC nanti) ----------
    _updateSessionTitle(text) {
        const active = this.state.sessions.find((s) => s.active);
        if (active && (active.title === "Percakapan baru" || !active.title)) {
            active.title = text.length > 38 ? text.slice(0, 38) + "…" : text;
        }
    }

    _escapeHtml(s) {
        return s
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    _dummyReply(text) {
        const t = text.toLowerCase();
        if (t.includes("request") && (t.includes("laptop") || t.includes("aset") || t.includes("barang"))) {
            return (
                "Untuk <b>request laptop / aset baru</b>:<br/>" +
                "<ol class='ai-ol'>" +
                "<li>Buka menu <b>IT → Form → Material Request</b> (barang habis pakai) atau <b>Asset Request</b> (aset tetap).</li>" +
                "<li>Klik <b>Create</b>, pilih karyawan &amp; kategori aset.</li>" +
                "<li>Isi alasan kebutuhan, lalu klik <b>Submit → Approve</b>.</li>" +
                "<li>Tim IT akan mem-fulfill dan menjadwalkan <b>Handover</b>.</li>" +
                "</ol>" +
                "Mau saya buatkan draf alasan request-nya? Tuliskan saja kebutuhan &amp; divisinya 🙂"
            );
        }
        if (t.includes("printer") || t.includes("print") || t.includes("epson")) {
            return (
                "<b>Troubleshooting printer tidak bisa print:</b><br/>" +
                "<ol class='ai-ol'>" +
                "<li>Cek kabel power/USB &amp; status lampu indikator.</li>" +
                "<li>Cek antrean print di PC (clear queue) &amp; set sebagai default printer.</li>" +
                "<li>Restart spooler / PC, coba test page.</li>" +
                "<li>Jika masih gagal, catat error-nya &amp; buat <b>Damage Report</b> di modul IT.</li>" +
                "</ol>" +
                "Boleh sebutkan tipe printer &amp; pesan error-nya? Saya pandu lebih detail."
            );
        }
        if (t.includes("damage") || t.includes("rusak") || t.includes("hilang")) {
            return (
                "Alur <b>Damage Report</b>:<br/>" +
                "<ol class='ai-ol'>" +
                "<li>Buka <b>IT → Form → Damage Report → Create</b>.</li>" +
                "<li>Pilih aset, tipe kerusakan (fisik / sistem / hilang), &amp; deskripsi.</li>" +
                "<li>Klik <b>Confirm Damage</b> — kondisi aset otomatis jadi <i>broken</i>.</li>" +
                "<li>Minta verifikasi atasan &amp; tunggu status <i>Resolved</i> dari IT.</li>" +
                "</ol>"
            );
        }
        if (t.includes("handover") || t.includes("serah terima") || t.includes("handover")) {
            return (
                "Alur <b>Item Handover</b>:<br/>" +
                "<ol class='ai-ol'>" +
                "<li>Buka <b>IT → Form → Item Handover → Create</b>.</li>" +
                "<li>Pilih penerima, tanggal, tambahkan line Aset / Consumable.</li>" +
                "<li>Klik <b>Sign &amp; Confirm</b> — stok consumable otomatis berkurang &amp; aset ter-assign.</li>" +
                "</ol>" +
                "Mau handover ke siapa? Saya bantu siapkan checklist-nya."
            );
        }
        if (t.includes("halo") || t.includes("hai") || t.includes("pagi") || t.includes("siang") || t.includes("sore")) {
            return "Halo juga! 👋 Senang bisa membantu.<br/>Coba tanya misalnya: <i>“cara request mouse baru”</i> atau <i>“wifi lemot gimana solusinya?”</i>";
        }
        return (
            "Baik, saya catat: <i>“" +
            this._escapeHtml(text.length > 120 ? text.slice(0, 120) + "…" : text) +
            "”</i><br/><br/>" +
            "Ini masih <b>mode tampilan (front-end)</b> — jawaban cerdas tersambung backend nanti. " +
            "Sementara itu saya bisa pandu seputar:<br/>" +
            "• Request / handover aset &nbsp;• Damage report &nbsp;• Troubleshooting umum<br/><br/>" +
            "Coba klik salah satu saran cepat di atas 👆 atau jelaskan lebih detail kebutuhanmu."
        );
    }
}

ITAskAI.template = "it_asset.AskAI";
registry.category("actions").add("it_asset_ask_ai_action", ITAskAI);
