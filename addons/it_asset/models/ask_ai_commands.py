# -*- coding: utf-8 -*-
"""Ask AI Commands & Flows — pola CALM yang diport ke Python tanpa LLM.

Peran file ini (cermin CALM Rasa, tanpa butuh model bahasa):

A. **Command schema** — NLU lokal hanya boleh mengeluarkan *perintah* dari
   daftar tertutup (``COMMAND_SLOTS``) dengan slot yang lolos validasi
   struktural. Perintah tak-valid tidak pernah mencapai tool.
B. **Flow engine data-driven** — interaksi multi-langkah (konfirmasi
   kandidat, klarifikasi unit, pencarian terpandu) didefinisikan sebagai
   DATA (``FLOW_DEFS``), bukan ``if`` tersebar. Runner murni
   (``flow_next``) beroperasi di atas dict biasa sehingga bisa diuji
   tanpa Odoo.
C. **Repair detection** — koreksi (``ralat/maksud saya``), pembatalan
   (``batal/gajadi``), dan lanjutan (``lanjut/kembali``) terdeteksi
   sebagai sinyal eksplisit sebelum klasifikasi normal.

File ini murni stdlib (tanpa ``import odoo``) sehingga bisa diuji mandiri::

    python ask_ai_commands.py
"""

import json as _json
import re as _re

# ============================================================================
# A. Command schema — allowlist perintah + slot
# ============================================================================

# Intent yang BOLEH dieksekusi sebagai tool. Intent sosial/OOD tidak ada di
# sini (mereka fast-path di backend, bukan command).
COMMAND_TOOLS = frozenset([
    "recap", "check_stock", "asset_search", "asset_detail", "asset_user",
    "asset_history", "maintenance_list", "handover_list", "damage_list",
    "request_status", "human_agent", "unit_detail", "asset_top",
    "identity", "creator",
])

# Slot yang boleh dibawa tiap command. Slot di luar daftar ini dibuang
# (cermin CALM: model tak bisa menyelundupkan parameter tak dikenal).
COMMAND_SLOTS = {
    "recap": (),
    "check_stock": ("item", "category", "radio_kind", "low_only",
                    "location"),
    "asset_search": ("category", "radio_kind", "asset_type", "state",
                     "condition", "item"),
    "asset_detail": ("asset_refs", "item", "category"),
    "asset_user": ("asset_refs", "employee_name", "category", "asset_type",
                   "radio_kind", "state", "condition", "item"),
    "asset_history": ("asset_refs", "item"),
    "maintenance_list": (),
    "handover_list": ("form_kind", "form_status", "period", "asset_refs"),
    "damage_list": ("form_status", "period", "asset_refs"),
    "request_status": ("form_kind", "form_status", "period"),
    "human_agent": (),
    "unit_detail": ("asset_refs", "category"),
    "asset_top": ("top_kind", "category", "asset_type", "radio_kind"),
    "identity": (),
    "creator": (),
}

# Minimal satu slot grup ini harus terisi agar command dianggap lengkap
# secara struktural (keberadaan di DB dicek level C di backend).
COMMAND_ANY_OF = {
    "asset_detail": ("asset_refs", "item", "category"),
    "asset_user": ("asset_refs", "employee_name", "category", "item"),
    "asset_history": ("asset_refs", "item"),
    "unit_detail": ("asset_refs", "category"),
    "asset_search": ("category", "radio_kind", "asset_type", "state",
                     "condition", "item"),
}

_VALID_PERIODS = frozenset(
    ["today", "yesterday", "this_week", "this_month", "last_month"])
_VALID_FORM_STATUS = frozenset(
    ["draft", "submitted", "approved", "partially_fulfilled", "fulfilled",
     "rejected", "signed", "confirmed", "resolved", "open", "done"])
_VALID_FORM_KIND = frozenset(
    ["handover", "material", "asset_request", "account"])
_VALID_TOP_KIND = frozenset(["oldest", "newest", "moved", "damaged"])
_VALID_STATE = frozenset(["available", "in_use", "maintenance", "retired"])
_VALID_CONDITION = frozenset(["good", "degraded", "broken"])
_VALID_RADIO_KIND = frozenset(["rig", "ht"])
_VALID_ASSET_TYPE = frozenset(["it", "operation"])

_RE_REF_LIKE = _re.compile(r"^[A-Z0-9][A-Z0-9 .\-_/]*[0-9][A-Z0-9/\-]*$")


def _is_ref_like(value):
    """True bila string mirip kode ref (ada digit, min 3 alnum)."""
    v = (value or "").strip().upper()
    alnum = _re.sub(r"[^A-Z0-9]", "", v)
    return len(alnum) >= 3 and any(ch.isdigit() for ch in alnum) \
        and bool(_RE_REF_LIKE.match(v))


def validate_slot(name, value):
    """Validasi struktural satu slot. Kembalikan error str atau ''."""
    if name == "asset_refs":
        vals = value if isinstance(value, (list, tuple)) else [value]
        vals = [v for v in vals if (v or "").strip()]
        if not vals:
            return "empty"
        for v in vals:
            if not _is_ref_like(v):
                return "bad ref format: %s" % v
        return ""
    if name in ("item", "category", "location"):
        return "" if len((value or "").strip()) >= 2 else "too short"
    if name == "employee_name":
        return "" if len((value or "").strip()) >= 3 else "too short"
    if name == "period":
        return "" if value in _VALID_PERIODS else "unknown period"
    if name == "form_status":
        return "" if value in _VALID_FORM_STATUS else "unknown status"
    if name == "form_kind":
        return "" if value in _VALID_FORM_KIND else "unknown kind"
    if name == "top_kind":
        return "" if value in _VALID_TOP_KIND else "unknown rank"
    if name == "state":
        return "" if value in _VALID_STATE else "unknown state"
    if name == "condition":
        return "" if value in _VALID_CONDITION else "unknown condition"
    if name == "radio_kind":
        return "" if value in _VALID_RADIO_KIND else "unknown radio kind"
    if name == "asset_type":
        return "" if value in _VALID_ASSET_TYPE else "unknown asset type"
    if name == "low_only":
        return "" if isinstance(value, bool) else "not bool"
    return "unknown slot"


def build_command(intent, entities, constraints=None):
    """Bangun command tervalidasi dari hasil NLU.

    ``entities``: dict extractor (boleh ada kunci ekstra — dibuang bila
    tak dikenal command). ``constraints``: dict kanal terpisah (low_only).

    Kembalikan ``{command, slots, valid, errors}``. ``valid=False`` berarti
    backend harus klarifikasi, bukan eksekusi.
    """
    errors = []
    if intent not in COMMAND_TOOLS:
        return {"command": intent, "slots": {}, "valid": False,
                "errors": ["not a tool command: %s" % intent]}
    allowed = COMMAND_SLOTS.get(intent, ())
    slots = {}
    for key in allowed:
        if key == "low_only":
            low = bool((constraints or {}).get("low_only", False))
            if low:
                slots[key] = True
            continue
        val = (entities or {}).get(key)
        if val is None or val == "" or val == []:
            continue
        err = validate_slot(key, val)
        if err:
            errors.append("%s: %s" % (key, err))
            continue
        slots[key] = val
    for key in COMMAND_ANY_OF.get(intent, ()):
        if slots.get(key):
            break
    else:
        if COMMAND_ANY_OF.get(intent):
            errors.append("missing slot: need one of %s"
                          % "/".join(COMMAND_ANY_OF[intent]))
    return {"command": intent, "slots": slots,
            "valid": not errors, "errors": errors}


# ============================================================================
# Repair detection — sinyal eksplisit sebelum klasifikasi normal
# ============================================================================

# HANYA kata batal eksplisit. "sudah/cukup/selesai" SENGAJA dikecualikan
# agar "material yang sudah fulfilled" tak terbaca sebagai pembatalan.
_RE_CANCEL = _re.compile(
    r"\b(batal|batalkan|gajadi|gak\s*jadi|nggak\s*jadi|tidak\s*jadi|"
    r"cancel|cancelled)\b", _re.I)
_RE_RESUME = _re.compile(
    r"^\W*(lanjut|lanjutkan|teruskan|terus|kembali|balik|resume)\W*$", _re.I)
_RE_CORRECT = _re.compile(
    r"^\W*(ralat|koreksi|revisi|maksud\s*saya|maksudnya|salah\s*ketik|"
    r"salah|sori|sorry|eh)\b[\s,:.\-]*", _re.I)
_RE_NEGATE = _re.compile(r"^\W*bukan\b[\s,:.\-]*", _re.I)


def detect_cancel(text):
    """True bila user membatalkan alur yang sedang berjalan."""
    return bool(_RE_CANCEL.search(text or ""))


def detect_resume(text):
    """True bila user minta lanjutkan alur yang tertunda."""
    return bool(_RE_RESUME.match((text or "").strip()))


def detect_correction(text):
    """Kembalikan payload koreksi (str) atau '' bila bukan koreksi.

    'ralat PRN-02' -> 'PRN-02'. 'bukan ITLT-007, maksudnya ITLT-008'
    -> 'ITLT-008' (ambil segmen terakhir setelah koma).
    """
    t = (text or "").strip()
    m = _RE_CORRECT.match(t) or _RE_NEGATE.match(t)
    if not m:
        return ""
    payload = t[m.end():].strip(" ,:.-")
    if "," in payload:  # 'bukan X, maksudnya Y' -> pakai Y
        payload = payload.rsplit(",", 1)[-1].strip()
        payload = _RE_CORRECT.sub("", payload).strip(" ,:.-")
        payload = _RE_NEGATE.sub("", payload).strip(" ,:.-")
    return payload


# Jawaban "semua/ya" atas pertanyaan konfirmasi (pindah dari ask_ai.py agar
# terpusat dan teruji; pola regex dipertahankan identik).
_RE_SHOW_ALL = _re.compile(
    r"^\W*(tampilkan\s+semua|tampil\s+semua|lihat\s+semua|tunjukkan\s+semua|"
    r"semua|semuanya|all|ya|iya|oke|ok|mau|boleh)(\W*)$", _re.I)


def detect_show_all(text):
    return bool(_RE_SHOW_ALL.match((text or "").strip()))


# ============================================================================
# F. FLOW_DEFS — definisi flow sebagai DATA
# ============================================================================

# Satu flow = {"slots": [...slot yang dikumpulkan...],
#              "steps": [langkah berurutan],
#              "on_complete": command yang dijalankan saat slot lengkap,
#              "prompts": {step: template}}.
# Runner generik di backend mengeksekusi definisi ini; menambah flow
# read-only baru = tambah entri + test, tanpa sentuh runner.
FLOW_DEFS = {
    # Konfirmasi multi-kandidat: "tag spesifik atau semua?"
    "confirm_asset": {
        "description": "Confirm which candidate asset the user means",
        "slots": ["action", "ids", "label"],
        "steps": ["await_choice"],
        "transitions": ["show_all", "show_one", "correction", "cancel",
                        "new_topic", "resume"],
    },
    # Klarifikasi unit: kode polos -> tanya mau info apa
    "unit_clarify": {
        "description": "Ask which unit info the user wants",
        "slots": ["ids", "label"],
        "steps": ["await_info"],
        "transitions": ["info_brand", "info_status", "info_assets",
                        "info_history", "show_all", "correction", "cancel",
                        "new_topic", "resume"],
    },
    # F: pencarian terpandu barang rusak (collect kategori -> eksekusi).
    # Contoh flow baru yang 100% dari data: runner + command asset_search
    # yang sudah ada, tanpa kode baru di backend.
    "guided_broken": {
        "description": "Guided search for broken assets by category",
        "slots": ["category"],
        "steps": ["await_category"],
        "on_complete": "asset_search",
        "fixed_slots": {"condition": "broken"},
        "prompts": {
            "await_category":
                "Mau cari aset rusak kategori apa? 🙏<br/>Contoh: "
                "<i>“laptop”</i>, <i>“printer”</i>, <i>“radio”</i>.",
        },
        "transitions": ["collect", "cancel", "new_topic", "resume"],
    },
}

# Kata info unit -> transisi unit_clarify (dipakai runner + test).
_UNIT_INFO_WORDS = {
    "info_brand": ("merek", "merk", "brand", "model"),
    "info_status": ("status", "kondisi"),
    "info_assets": ("aset", "radio", "terpasang", "pasang"),
    "info_history": ("riwayat", "sejarah", "history"),
}


def unit_info_transition(text):
    """Petakan kata info unit -> nama transisi, atau ''."""
    low = (text or "").lower()
    for name, words in _UNIT_INFO_WORDS.items():
        if any(w in low for w in words):
            return name
    return ""


def flow_signal(flow_name, state, text, ref_hit=False, info=""):
    """Petakan pesan user -> sinyal flow_next (murni, tanpa I/O).

    ``ref_hit``: ref user cocok kandidat flow (dihitung backend dari DB).
    ``info``: transisi info unit yang sudah dipetakan (atau '').
    Cermin pemetaan di backend ``_consume_flow`` — keduanya harus sejalan.
    """
    t = (text or "").strip()
    if detect_cancel(t):
        return "cancel"
    if detect_resume(t):
        return "resume"
    if detect_correction(t):
        return "correction"
    if detect_show_all(t) and len(t) <= 40:
        return "show_all"
    if flow_name == "confirm_asset":
        return "show_one" if ref_hit else "new_topic"
    if flow_name == "unit_clarify":
        if info:
            return info
        return "show_one" if ref_hit else "new_topic"
    if flow_name == "guided_broken":
        return "collect"
    return "new_topic"


def flow_next(flow_name, state, signal, payload=None):
    """Runner MURNI: (flow, state, sinyal) -> keputusan transisi.

    ``state``: dict {step, slots}. ``signal`` salah satu dari:
    show_all | show_one | info_* | collect | correction | cancel |
    new_topic | resume.
    ``payload``: data tambahan (mis. slot terkumpul / teks koreksi).

    Kembalikan ``{action, step?, slots?}`` dengan action salah satu dari:
    show_all | show_one | ask | execute | restart | close | push | resume.
    Tanpa I/O dan tanpa Odoo — bisa diunit-test penuh.
    """
    flow = FLOW_DEFS.get(flow_name)
    if not flow:
        return {"action": "close"}
    if signal == "cancel":
        return {"action": "close"}
    if signal == "new_topic":
        return {"action": "push"}
    if signal == "resume":
        return {"action": "resume", "step": state.get("step"),
                "slots": dict(state.get("slots") or {})}
    if signal == "correction":
        slots = dict(state.get("slots") or {})
        if payload and isinstance(payload, dict):
            slots.update(payload)
        # koreksi mengulang step saat ini dengan slot baru
        return {"action": "restart", "step": state.get("step"), "slots": slots,
                "correction_text": payload if isinstance(payload, str) else ""}
    if signal in ("show_all", "show_one") or signal.startswith("info_"):
        return {"action": signal, "step": state.get("step"),
                "slots": dict(state.get("slots") or {}),
                "payload": payload}
    if signal == "collect":
        slots = dict(state.get("slots") or {})
        if payload and isinstance(payload, dict):
            slots.update(payload)
        steps = flow.get("steps") or []
        cur = state.get("step") or (steps[0] if steps else "")
        # slot wajib flow ini sudah lengkap?
        need = [s for s in (flow.get("slots") or []) if not slots.get(s)]
        if not need:
            return {"action": "execute", "slots": slots,
                    "command": flow.get("on_complete"),
                    "fixed_slots": dict(flow.get("fixed_slots") or {})}
        try:
            nxt = steps[steps.index(cur) + 1] if cur in steps else steps[0]
        except IndexError:
            nxt = steps[-1]
        return {"action": "ask", "step": nxt, "slots": slots,
                "prompt": (flow.get("prompts") or {}).get(nxt, "")}
    return {"action": "close"}


# ============================================================================
# G. Conversation relation — hubungkan pesan baru ke turn sebelumnya
#    (goals.md §2-5). MURNI (tanpa I/O/Odoo), cermin gaya flow_signal di atas:
#    backend menghitung Üintent/entity mentah, sini yang memutuskan hubungan
#    + penggabungan. Keduanya harus sejalan dengan pemakaian di backend
#    ``_answer_inner`` (resolusi konteks).
# ============================================================================

REL_NEW = "new"
REL_FOLLOW_UP = "follow_up"
REL_REFINE = "refine"
REL_CORRECT = "correct"
REL_REPLACE = "replace"
REL_CONTINUE = "continue"
REL_CONFIRM = "confirm"
REL_DENY = "deny"
REL_CANCEL = "cancel"
REL_SAME_TOPIC = "same_topic"

RELATIONS = (REL_NEW, REL_FOLLOW_UP, REL_REFINE, REL_CORRECT, REL_REPLACE,
             REL_CONTINUE, REL_CONFIRM, REL_DENY, REL_CANCEL, REL_SAME_TOPIC)

# Pertanyaan lanjutan ala "yang rusak?", "di gudang mana?", "yang Wolo?".
_RE_FOLLOW_QUESTION = _re.compile(
    r"^[\s\W]*(yang|yg|di|ke|dari|untuk|dengan|terus|trus|lalu)\b", _re.I)
# Pengulangan/penegasan tanpa info baru: "berapa?", "berapa".
_RE_BARE_BERAPA = _re.compile(
    r"^[\s\W]*(berapa|berapaan|brp)[\s\W?]*$", _re.I)
# Penolakan singkat tanpa flow ("bukan", "salah") — dicatat, tanpa aksi khusus.
_RE_DENY = _re.compile(
    r"^[\s\W]*(bukan|salah|tidak|gak|nggak)[\s\W]*$", _re.I)
# Kata kerja query: bila ada -> pesan membawa topik sendiri (bukan refine).
_REL_QUERY_WORDS = frozenset(
    "stok stock berapa sisa habis menipis restock cari carikan lihat liat "
    "tampil tampilkan tunjukkan daftar list rekap ringkas riwayat siapa "
    "pengguna cek ada".split())


def detect_relation(text, prev=None):
    """Petakan pesan baru -> (relation, payload). Murni, tanpa I/O.

    ``prev``: dict konteks turn lalu {intent, item} (boleh kosong).
    Tanpa konteks lalu -> selalu NEW. Repair eksplisit (batal/lanjut/
    ralat/semua) dipetakan dulu agar sejalan dengan flow engine.
    """
    prev = prev or {}
    t = (text or "").strip()
    if detect_cancel(t):
        return REL_CANCEL, {}
    if detect_resume(t):
        return REL_CONTINUE, {}
    corr = detect_correction(t)
    if corr:
        return REL_CORRECT, {"text": corr}
    if detect_show_all(t) and len(t) <= 40:
        return REL_CONFIRM, {}
    if not (prev.get("intent") or ""):
        return REL_NEW, {}
    if _RE_BARE_BERAPA.match(t):
        # "berapa?" setelah "stok kabel" = jalankan konteks terakhir
        return REL_CONTINUE, {}
    if _RE_DENY.match(t):
        return REL_DENY, {}
    if _RE_FOLLOW_QUESTION.match(t):
        # "yang rusak?", "di gudang mana?", "yang Wolo?"
        return REL_FOLLOW_UP, {}
    words = [w for w in _re.sub(r"[?.,!]+$", "", t.lower()).split()
             if len(w) > 1]
    if words and not (set(words) & _REL_QUERY_WORDS) and len(words) <= 4:
        # frasa benda pendek tanpa kata query: "kabel antena", "antena"
        # setelah "stok kabel" = persempit topik terakhir
        return REL_REFINE, {"text": t}
    if words and (set(words) & _REL_QUERY_WORDS):
        # query lengkap yang baru: "stok tinta" setelah "stok kabel"
        return REL_REPLACE, {}
    return REL_SAME_TOPIC, {}


def refine_item(prev_item, new_item):
    """Gabung item lama + kata baru (dedup, urutan lama dulu).

    'kabel'+'antena' -> 'kabel antena'; 'kabel antena'+'antena' tetap.
    Kosong salah satu -> yang terisi.
    """
    prev_words = (prev_item or "").split()
    new_words = (new_item or "").split()
    if not prev_words:
        return (new_item or "").strip()
    if not new_words:
        return (prev_item or "").strip()
    out = list(prev_words)
    for w in new_words:
        if w.lower() not in {x.lower() for x in out}:
            out.append(w)
    return " ".join(out)


_CORRECTION_MARKERS = _re.compile(
    r"^(maksud\s*saya|maksudnya|maksud|saya|eh)[\s,.:;\-]+", _re.I)


def clean_correction_item(item):
    """Kupas penanda koreksi dari awal item ('maksud radio base'->'radio base')."""
    s = (item or "").strip()
    while True:
        shorter = _CORRECTION_MARKERS.sub("", s).strip(" ,:.-")
        if shorter == s or not shorter:
            break
        s = shorter
    return s


# Kata modifier (kondisi/status/stok/lokasi) bukan nama barang — dikupas
# dari item baru saat follow-up/refine agar "yang rusak?" tak menimpa item
# "kabel antena" menjadi "rusak". Cermin STATE/CONDITION/LOW_ONLY NLU.
_REFINE_NOISE = frozenset(
    "rusak broken degraded lemot lambat mati pecah hancur "
    "bagus baik normal good "
    "tersedia available ready dipakai digunakan terpakai "
    "menipis habis restock minimum rendah kosong kurang "
    "wolo gudang lokasi warehouse site gdg".split())


def strip_modifiers(item):
    """Buang kata modifier dari item ('kabel rusak'->'kabel', 'rusak'->'')."""
    return " ".join(
        w for w in (item or "").split() if w.lower() not in _REFINE_NOISE
    ).strip()


def resolve_context(text, classification, current, prev):
    """Gabungkan turn baru dengan konteks sesi. Murni, tanpa I/O.

    ``classification``: {intent, confidence, method} turn ini (mentah).
    ``current``: {entities, constraints} turn ini (mentah).
    ``prev``: {intent, entities, constraints} turn lalu (boleh {}).

    Kembalikan (intent, entities, constraints, relation). Aturan:
    - tanpa konteks lalu -> (asli, NEW).
    - CORRECT: item diganti payload koreksi, intent ikut turn lalu (bila tool).
    - CONTINUE/FOLLOW_UP + metode lemah -> warisi intent turn lalu.
    - FOLLOW_UP + intent tool kuat (mis. asset_search 'yang rusak?') ->
      intent dipertahankan, slot kosong diwarisi (item/lokasi/kategori).
    - REFINE: item digabung (prev + kata baru); intent ikut turn ini bila
      tool, else turn lalu.
    - REPLACE/NEW/SAME_TOPIC/DENY/CONFIRM/CANCEL -> apa adanya (backend
      menangani repair/flow duluan; di sini hanya NEW/REPLACE jujur).
    """
    intent0 = (classification or {}).get("intent") or "unknown"
    ent0 = dict((current or {}).get("entities") or {})
    con0 = dict((current or {}).get("constraints") or {"low_only": False})
    pintent = (prev or {}).get("intent") or ""
    pent = dict((prev or {}).get("entities") or {})
    pcon = dict((prev or {}).get("constraints") or {})
    relation, payload = detect_relation(
        text, {"intent": pintent, "item": pent.get("item", "")})
    if not pintent:
        return intent0, ent0, con0, REL_NEW
    if relation == REL_CORRECT:
        item = clean_correction_item(
            payload.get("text", "") or ent0.get("item", ""))
        ent = dict(pent)
        for key, val in ent0.items():
            if val or key not in ent:
                ent[key] = val
        if item:
            ent["item"] = item
        intent = pintent if pintent in COMMAND_TOOLS else intent0
        return intent, ent, con0, relation
    if relation == REL_CONTINUE:
        if pintent in COMMAND_TOOLS:
            ent = dict(pent)
            con = dict(pcon)
            if con0.get("low_only"):
                con["low_only"] = True
            return pintent, ent, con, relation
        return intent0, ent0, con0, REL_NEW
    if relation == REL_FOLLOW_UP:
        ent = dict(pent)
        for key, val in ent0.items():
            if key == "item":
                continue  # item ditangani di bawah (anti-cemar modifier)
            if val:
                ent[key] = val
        new_bits = strip_modifiers(ent0.get("item", ""))
        ent["item"] = refine_item(pent.get("item", ""), new_bits) \
            if new_bits else pent.get("item", "")
        con = dict(pcon)
        if con0.get("low_only"):
            con["low_only"] = True
        if intent0 in COMMAND_TOOLS and (classification or {}).get(
                "method") in ("rule", "override"):
            # sinyal kuat ("yang rusak?" -> asset_search): intentnya dipakai,
            # slot kosong (item/lokasi/...) diwarisi dari konteks
            return intent0, ent, con, relation
        if pintent in COMMAND_TOOLS:
            return pintent, ent, con, relation
        return intent0, ent0, con0, REL_NEW
    if relation == REL_REFINE:
        item = refine_item(pent.get("item", ""),
                           strip_modifiers(ent0.get("item", "")))
        ent = dict(pent)
        for key, val in ent0.items():
            if key == "item":
                continue
            if val:
                ent[key] = val
        ent["item"] = item
        con = dict(pcon)
        if con0.get("low_only"):
            con["low_only"] = True
        if intent0 in COMMAND_TOOLS:
            return intent0, ent, con, relation
        if pintent in COMMAND_TOOLS:
            return pintent, ent, con, relation
        return intent0, ent0, con0, REL_NEW
    return intent0, ent0, con0, (
        REL_REPLACE if relation == REL_REPLACE else REL_NEW)


def dump_state(state):
    """Serialisasi state flow ke JSON string (disimpan di sesi)."""
    try:
        return _json.dumps(state or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def load_state(raw):
    """Balikan dump_state; rusak -> {} (flow dianggap tidak ada)."""
    try:
        data = _json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ============================================================================
# F1/F2/F4 helpers — guard anti-halusinasi (murni, teruji)
# ============================================================================

def _stripped(text):
    """Kanonik perbandingan: alnum saja, uppercase (duplikat ringan agar
    modul ini tetap mandiri tanpa impor nlu)."""
    return _re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def is_grounded_code(code, original_text):
    """True bila kode boleh DISEBUT ke user: stripped-nya substring dari
    pertanyaan ASLI user. F1: template slot_repair dilarang menuduh user
    mencari kode yang tak pernah diketiknya, dari sumber mana pun."""
    key = _stripped(code)
    if not key:
        return False
    return key in _stripped(original_text)


_RE_CODE_LIKE = _re.compile(r"[A-Z]{2,}-[0-9][A-Z0-9/\-]*|\b\d{2,}\b")


def invented_codes(original_html, polished_html):
    """Kode/angka di hasil poles yang TAK ADA di aslinya (F2).

    Kembalikan list temuan. Kosong = aman. Guardrail ini cerminan terbalik
    dari preservation check: bukan hanya 'fakta asli tak boleh hilang',
    tapi 'fakta baru tak boleh muncul' (khususnya kode aset inventaris).
    """
    def _toks(html):
        txt = _re.sub(r"<[^>]*>", " ", html or "")
        return set(_RE_CODE_LIKE.findall(txt.upper()))

    return sorted(_toks(polished_html) - _toks(original_html))


_RE_SELF_CORRECT = _re.compile(
    r"\b(halu|halusinasi|ngawur|ngaco)\b", _re.I)


def detect_self_correct(text):
    """True bila user menegur bot mengarang (F4: momen minta maaf)."""
    return bool(_RE_SELF_CORRECT.search(text or ""))


# F6b: intent sosial via LLM wajib grounded kata sosial di teks user.
# Qwen 0.6B kadang menebak 'identity' untuk gumaman bingung
# ("aduh gimana ya") — tanpa jangkar kata, tebakan itu ditolak.
_SOCIAL_KEYWORDS = {
    "greeting": ("halo", "hai", "hello", "hei", "hey", "pagi", "siang",
                 "sore", "malam", "assalamu", "kabar"),
    "thanks": ("kasih", "makasih", "thanks", "nuwun", "suwun", "thx"),
    "goodbye": ("bye", "dadah", "jumpa", "tinggal"),
    "help": ("bisa", "bantu", "contoh", "fitur", "cara", "help", "menu"),
    "identity": ("kamu", "anda", "nama", "fungsi", "tugas", "peran",
                 "siapa", "bot", "robot"),
    "creator": ("buat", "pembuat", "developer", "pencipta", "cipta",
                 "dibuat", "bikin"),
    "compliment": ("pintar", "pinter", "hebat", "keren", "mantap",
                   "bagus banget", "luar biasa", "terbaik", "the best",
                   "good job", "membantu"),
    "flirt": ("cantik", "ganteng", "cute", "imut", "manis", "single",
              "jomblo"),
    "romantic": ("suka", "cinta", "sayang", "rindu", "nikah", "pacar",
                 "jadian"),
    "poetic": ("puisi", "pantun", "sajak", "syair", "bait", "rima"),
    "joke": ("lawak", "lucu", "lelucon", "tebakan", "ketawa", "ngakak",
             "wkwk", "haha", "humor", "joke"),
    "insult": ("bodoh", "bego", "goblok", "tolol", "dungu", "idiot",
               "jelek", "benci", "nyebelin", "menyebalkan", "payah",
               "sampah", "brengsek", "dasar", "kamu rusak", "kamu lemot",
               "bot rusak"),
    "casual": ("lagi apa", "sedang apa", "makan", "gabut", "bosen",
               "bosan", "btw", "wkwk", "haha", "hehe", "oh gitu"),
    "human_agent": ("admin", "teknisi", "cs", "manusia", "orang"),
}


def social_grounded(intent, text):
    """True bila intent sosial punya jangkar kata di teks user.

    Kata kunci <=2 huruf ('cs') wajib cocok batas-kata penuh.
    """
    low = (text or "").lower()
    for k in _SOCIAL_KEYWORDS.get(intent, ()):
        if len(k) <= 2:
            if _re.search(r"\b" + _re.escape(k) + r"\b", low):
                return True
        elif k in low:
            return True
    return False


# F3b: OOD deterministik (pada teks pra-kamus) tak boleh dikalahkan oleh
# koreksi kamus / NLU lemah / LLM yang ragu-ragu. Hanya rule/override
# (sinyal kuat) dan LLM yang YAKIN (jalur EXECUTE) boleh menang.
_OOD_STICKY_METHODS = frozenset(["empty", "dict", "nlu", "llm_ungrounded"])


def ood_wins_over(method, route_execute):
    """True bila vonis OOD mentah harus dipakai alih-alih pipeline lanjut."""
    if method in _OOD_STICKY_METHODS:
        return True
    if method == "llm" and not route_execute:
        return True
    return False


# ============================================================================
# Self-test mandiri (aturan tetap: ukur, bukan latih)
# ============================================================================

_SELF_TEST_COMMANDS = [
    # (intent, entities, constraints, valid?)
    ("asset_detail", {"asset_refs": ["ITLT-007"]}, {}, True),
    ("asset_detail", {"item": "kabel"}, {}, True),
    ("asset_detail", {}, {}, False),
    ("asset_user", {"employee_name": "budi"}, {}, True),
    ("asset_user", {"employee_name": "x"}, {}, False),
    ("asset_history", {"asset_refs": ["PRN-01"]}, {}, True),
    ("unit_detail", {"asset_refs": ["DT-02"]}, {}, True),
    ("unit_detail", {}, {}, False),
    ("check_stock", {"item": "tinta"}, {"low_only": True}, True),
    ("check_stock", {}, {}, True),
    ("asset_search", {"category": "laptop", "state": "available"}, {}, True),
    ("asset_search", {}, {}, False),
    ("asset_search", {"state": "heng"}, {}, False),
    ("request_status", {"form_status": "open", "period": "this_month"}, {}, True),
    ("request_status", {"form_status": "ngambang"}, {}, False),
    ("asset_top", {"top_kind": "moved"}, {}, True),
    ("handover_list", {"period": "kemarin"}, {}, False),
    ("greeting", {}, {}, False),  # bukan tool command
    ("unknown", {}, {}, False),
]

_SELF_TEST_REPAIR = [
    ("batal", "cancel"), ("batalkan ya", "cancel"), ("gajadi deh", "cancel"),
    ("cancel", "cancel"), ("rekap aset", None), ("sudah fulfilled", None),
    ("lanjut", "resume"), ("lanjutkan", "resume"), ("kembali", "resume"),
    ("stok radio", None),
    ("ralat PRN-02", "correction:PRN-02"),
    ("maksud saya ITLT-008", "correction:ITLT-008"),
    ("bukan ITLT-007, maksudnya ITLT-008", "correction:ITLT-008"),
    ("semua", "show_all"), ("ya", "show_all"), ("tampilkan semua", "show_all"),
    ("ITLT-007", None),
]

_SELF_TEST_FLOWS = [
    # (flow, state, signal, payload, expected_action)
    ("confirm_asset", {"step": "await_choice", "slots": {}},
     "cancel", None, "close"),
    ("confirm_asset", {"step": "await_choice", "slots": {}},
     "show_all", None, "show_all"),
    ("confirm_asset", {"step": "await_choice", "slots": {"action": "asset_user"}},
     "correction", {"asset_refs": ["PRN-02"]}, "restart"),
    ("confirm_asset", {"step": "await_choice", "slots": {}},
     "new_topic", None, "push"),
    ("unit_clarify", {"step": "await_info", "slots": {}},
     "info_brand", None, "info_brand"),
    ("guided_broken", {"step": "await_category", "slots": {}},
     "collect", {"category": "laptop"}, "execute"),
    ("guided_broken", {"step": "await_category", "slots": {}},
     "collect", {}, "ask"),
    ("nope", {}, "cancel", None, "close"),
]

_SELF_TEST_SIGNALS = [
    # (flow, text, ref_hit, info, expected_signal)
    ("confirm_asset", "semua", False, "", "show_all"),
    ("confirm_asset", "batal", False, "", "cancel"),
    ("confirm_asset", "ralat PRN-02", False, "", "correction"),
    ("confirm_asset", "ITLT-007", True, "", "show_one"),
    ("confirm_asset", "ITLT-007", False, "", "new_topic"),
    ("confirm_asset", "stok tinta", False, "", "new_topic"),
    ("unit_clarify", "mereknya apa", False, "info_brand", "info_brand"),
    ("unit_clarify", "DT-02", True, "", "show_one"),
    ("unit_clarify", "rekap aset", False, "", "new_topic"),
    ("guided_broken", "laptop", False, "", "collect"),
    ("guided_broken", "batal", False, "", "cancel"),
]


_SELF_TEST_RELATIONS = [
    # (text, prev_intent, prev_item, want_relation)
    ("berapa?", "check_stock", "kabel", REL_CONTINUE),
    ("kabel antena", "check_stock", "kabel", REL_REFINE),
    ("antena", "check_stock", "kabel", REL_REFINE),
    ("yang rusak?", "check_stock", "kabel", REL_FOLLOW_UP),
    ("yang menipis?", "check_stock", "kabel", REL_FOLLOW_UP),
    ("yang Wolo?", "check_stock", "kabel antena", REL_FOLLOW_UP),
    ("di gudang mana?", "check_stock", "kabel antena", REL_FOLLOW_UP),
    ("eh maksud saya radio base", "check_stock", "kabel", REL_CORRECT),
    ("stok tinta", "check_stock", "kabel", REL_REPLACE),
    ("rekap aset", "check_stock", "kabel", REL_REPLACE),
    ("batal", "check_stock", "kabel", REL_CANCEL),
    ("semua", "check_stock", "kabel", REL_CONFIRM),
    ("stok kabel", "", "", REL_NEW),
    ("berapa?", "", "", REL_NEW),
]

_SELF_TEST_RESOLVE = [
    # (text, classification, cur_entities, cur_constraints, prev, want_intent,
    #  want_item, want_relation)
    ("berapa?",
     {"intent": "unknown", "confidence": 0.0, "method": "empty"},
     {}, {}, {"intent": "check_stock",
              "entities": {"item": "kabel", "category": "kabel"},
              "constraints": {}},
     "check_stock", "kabel", REL_CONTINUE),
    ("kabel antena",
     {"intent": "check_stock", "confidence": 0.91, "method": "rule"},
     {"item": "kabel antena", "category": "kabel"}, {},
     {"intent": "check_stock", "entities": {"item": "kabel"},
      "constraints": {}},
     "check_stock", "kabel antena", REL_REFINE),
    ("antena",
     {"intent": "check_stock", "confidence": 0.91, "method": "rule"},
     {"item": "antena"}, {},
     {"intent": "check_stock", "entities": {"item": "kabel"},
      "constraints": {}},
     "check_stock", "kabel antena", REL_REFINE),
    ("yang rusak?",
     {"intent": "asset_search", "confidence": 0.90, "method": "rule"},
     {"condition": "broken"}, {},
     {"intent": "check_stock",
      "entities": {"item": "radio ht", "category": "radio"},
      "constraints": {}},
     "asset_search", "radio ht", REL_FOLLOW_UP),
    ("yang menipis?",
     {"intent": "check_stock", "confidence": 0.94, "method": "rule"},
     {}, {"low_only": True},
     {"intent": "check_stock",
      "entities": {"item": "kabel antena", "category": "kabel"},
      "constraints": {}},
     "check_stock", "kabel antena", REL_FOLLOW_UP),
    ("eh maksud saya radio base",
     {"intent": "asset_search", "confidence": 0.90, "method": "rule"},
     {"item": "maksud radio base", "category": "radio"}, {},
     {"intent": "check_stock", "entities": {"item": "kabel"},
      "constraints": {}},
     "check_stock", "radio base", REL_CORRECT),
    ("yang Wolo?",
     {"intent": "unknown", "confidence": 0.0, "method": "empty"},
     {"location": "Wolo"}, {},
     {"intent": "check_stock",
      "entities": {"item": "kabel antena", "category": "kabel"},
      "constraints": {}},
     "check_stock", "kabel antena", REL_FOLLOW_UP),
    ("yang antena?",
     {"intent": "unknown", "confidence": 0.0, "method": "empty"},
     {"item": "antena"}, {},
     {"intent": "check_stock",
      "entities": {"item": "kabel", "category": "kabel"},
      "constraints": {}},
     "check_stock", "kabel antena", REL_FOLLOW_UP),
]


def run_self_test():
    good, total, fails = 0, 0, []
    for intent, ent, con, want in _SELF_TEST_COMMANDS:
        total += 1
        got = build_command(intent, ent, con)["valid"]
        if got == want:
            good += 1
        else:
            fails.append(("cmd", intent, want, got))
    for text, want in _SELF_TEST_REPAIR:
        total += 1
        if want is None:
            got = None if not (detect_cancel(text) or detect_resume(text)
                               or detect_correction(text) or detect_show_all(text)) else "hit"
        elif want == "cancel":
            got = "cancel" if detect_cancel(text) else None
        elif want == "resume":
            got = "resume" if detect_resume(text) else None
        elif want == "show_all":
            got = "show_all" if detect_show_all(text) else None
        else:
            kind, payload = want.split(":", 1)
            got = ("%s:%s" % (kind, detect_correction(text))
                   if detect_correction(text) else None)
        if got == want:
            good += 1
        else:
            fails.append(("repair", text, want, got))
    for flow, state, sig, payload, want in _SELF_TEST_FLOWS:
        total += 1
        got = flow_next(flow, state, sig, payload).get("action")
        if got == want:
            good += 1
        else:
            fails.append(("flow", (flow, sig), want, got))
    for flow, text, ref_hit, info, want in _SELF_TEST_SIGNALS:
        total += 1
        got = flow_signal(flow, {"step": "", "slots": {}}, text,
                          ref_hit=ref_hit, info=info)
        if got == want:
            good += 1
        else:
            fails.append(("signal", (flow, text), want, got))
    # G (goals.md §9): relation pesan baru thd konteks sesi
    for text, pintent, pitem, want in _SELF_TEST_RELATIONS:
        total += 1
        got, _payload = detect_relation(
            text, {"intent": pintent, "item": pitem})
        if got == want:
            good += 1
        else:
            fails.append(("relation", (text, pintent), want, got))
    # G: resolusi konteks (intent + item warisan)
    for (text, clf, ent, con, prev, want_intent, want_item,
            want_rel) in _SELF_TEST_RESOLVE:
        total += 1
        got_intent, got_ent, _con, got_rel = resolve_context(
            text, clf, {"entities": ent, "constraints": con}, prev)
        if (got_intent == want_intent
                and (got_ent or {}).get("item") == want_item
                and got_rel == want_rel):
            good += 1
        else:
            fails.append(("resolve", text,
                          (want_intent, want_item, want_rel),
                          (got_intent, (got_ent or {}).get("item"),
                           got_rel)))
    # F1: kode hanya boleh disebut bila grounded di pertanyaan asli
    for code, original, want in [
        ("ITLT-007", "siapa pakai ITLT-007?", True),
        ("ITLT-007", "siapa pakai itlt 007?", True),
        ("DT-02", "riwayat dt 02", True),
        ("KCT-1K12", "kenapa langit warna biru?", False),
        ("CORE-5", "emangnya kapan aku cari kode ini", False),
        ("", "stok radio?", False),
    ]:
        total += 1
        got = is_grounded_code(code, original)
        if got == want:
            good += 1
        else:
            fails.append(("grounded", (code, original), want, got))
    # F2: hasil poles tak boleh memunculkan kode/angka baru
    for original, polished, want_empty in [
        ("Stok <b>5</b> tersedia", "Stok <b>5</b> tersedia ya", True),
        ("Data belum tercatat", "Kode <b>KCT-1K12</b> tidak ditemukan", False),
        ("Total <b>12</b> aset", "Total <b>12</b> aset, 15 rusak", False),
    ]:
        total += 1
        got = invented_codes(original, polished)
        if (not got) == want_empty:
            good += 1
        else:
            fails.append(("invented", polished, want_empty, got))
    # F4: teguran halu terdeteksi; status form bukan teguran
    for text, want in [
        ("kok kamu halu sih?", True), ("ngaco banget", True),
        ("jangan ngawur dong", True), ("material yang sudah fulfilled", False),
        ("rekap aset", False),
    ]:
        total += 1
        got = detect_self_correct(text)
        if got == want:
            good += 1
        else:
            fails.append(("selfcorrect", text, want, got))
    # F6b: intent sosial LLM wajib grounded kata sosial
    for intent, text, want in [
        ("identity", "kamu siapa?", True),
        ("identity", "aduh gimana ya", False),
        ("identity", "siapa namamu", True),
        ("greeting", "halo kak", True),
        ("greeting", "stok radio", False),
        ("help", "kamu bisa apa", True),
        ("creator", "siapa developernya", True),
        ("creator", "rekap aset", False),
        ("human_agent", "hubungi teknisi", True),
        ("thanks", "makasih ya", True),
        ("compliment", "kamu keren banget", True),
        ("compliment", "laptop bagus yang tersedia", False),
        ("flirt", "kamu cantik", True),
        ("romantic", "aku cinta kamu", True),
        ("poetic", "buatkan pantun", True),
        ("joke", "ceritakan lawakan", True),
        ("insult", "kamu bodoh", True),
        ("insult", "laptop rusak", False),
        ("casual", "lagi apa", True),
        ("casual", "rekap aset", False),
    ]:
        total += 1
        got = social_grounded(intent, text)
        if got == want:
            good += 1
        else:
            fails.append(("socialground", (intent, text), want, got))
    # F3b: OOD mentah menang atas jalur lemah/ragu, kalah dari sinyal kuat
    for method, route_exec, want in [
        ("empty", False, True), ("dict", False, True),
        ("nlu", False, True), ("llm_ungrounded", False, True),
        ("llm", False, True), ("llm", True, False),
        ("rule", True, False), ("rule", False, False),
        ("override", True, False), ("ood_rule", False, False),
    ]:
        total += 1
        got = ood_wins_over(method, route_exec)
        if got == want:
            good += 1
        else:
            fails.append(("oodwins", (method, route_exec), want, got))
    print("commands self-test: %d/%d benar (%.1f%%)"
          % (good, total, good / total * 100 if total else 0))
    for kind, case, want, got in fails:
        print("  FAIL [%s] %r harap=%r dapat=%r" % (kind, case, want, got))
    return good == total


if __name__ == "__main__":
    run_self_test()
