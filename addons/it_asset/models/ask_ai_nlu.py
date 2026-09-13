# -*- coding: utf-8 -*-
"""Ask AI NLU — port metode WACS (Go) ke Python untuk modul IT Odoo.

Arsitektur yang diport (lihat D:/10.PROJECT/wacs/intent.md):

1.  **Sumber kebenaran tunggal** — ``ALL_INTENTS``, ``INTENT_CATEGORY`` dan
    ``CONF_THRESHOLDS`` adalah satu-satunya definisi nama intent, kategori dan
    ambang confidence. Jangan mendeklarasikan ulang di tempat lain.
2.  **Tiga lapis** (``classify``):
    - L1 ``llm_decide`` — titik ekstensi model bahasa (punya kontrak
      ``{intent, entities, constraints, confidence}`` ala WACS ``Decide()``).
      Nonaktif secara default (``LLM_ENABLED = False``) sehingga selalu
      menyerah ke lapis deterministik — persis perilaku WACS saat runtime
      Qwen mati.
    - L2 ``asset_query_override`` — sinyal kuat (kode tag aset, kata stok +
      nama barang, kata rekap, riwayat + referensi) memaksa intent dengan
      confidence 1.0 tanpa lewat NLU. Hanya mengklaim objek IT
      (guard ``_names_non_it_topic``), cerminan ``CatalogQueryOverride``.
    - L3 ``Classifier`` — Layer 1 rule regex teks mentah, Layer 1b rule pada
      teks hasil ``normalize_id``, Layer 1c rule luar-domain eksplisit
      (``ood_match`` — dievaluasi SETELAH rule in-domain supaya pertanyaan IT
      selalu menang), Layer 2 TF-IDF cosine + gate
      (``MIN_NLU_SIMILARITY`` / ``NLU_WINNER_MARGIN`` / ``LONE_MATCH_FACTOR`` /
      ``MIN_DISTINCTIVE_IDF`` — angka kalibrasi disalin dari WACS).
3.  **Entity extractor terpisah dari constraints** — model/intent tidak boleh
    menyelundupkan filter; channel ``constraints`` (``low_only``) hanya diisi
    extractor, cerminan ``entity.Constraints`` WACS.
4.  **Tool hanya di backend Odoo** (``ask_ai.py``) — NLU tidak pernah menyentuh
    database dan tidak boleh mengarang fakta stok/aset/pengguna.
5.  **Routing confidence** (``route``) — cerminan ``confidenceRouting`` WACS:
    ``confidence < floor`` → handover ke staff IT; ``floor <= c < threshold``
    → klarifikasi; ``>= threshold`` → eksekusi tool.
6.  **Scope reply bervariasi** per alasan OOD + ``data_miss`` (cerminan
    ``storeMissReply``) + rotasi deterministik (hash + counter).
7.  **Antrean feedback** — ``capture_reason`` memutuskan pesan apa yang masuk
    kurasi (cerminan ``CaptureReasonFor`` WACS §17).

File ini murni stdlib (tanpa ``import odoo``) sehingga bisa diuji mandiri::

    python ask_ai_nlu.py
"""

import hashlib
import itertools
import math
import re

# ============================================================================
# 1. Sumber kebenaran tunggal: intent, kategori, threshold
# ============================================================================

INTENT_GREETING = "greeting"
INTENT_THANKS = "thanks"
INTENT_GOODBYE = "goodbye"
INTENT_HELP = "help"
INTENT_IDENTITY = "identity"
INTENT_CREATOR = "creator"
INTENT_UNIT_DETAIL = "unit_detail"
INTENT_ASSET_TOP = "asset_top"
INTENT_RECAP = "recap"
INTENT_CHECK_STOCK = "check_stock"
INTENT_ASSET_SEARCH = "asset_search"
INTENT_ASSET_DETAIL = "asset_detail"
INTENT_ASSET_USER = "asset_user"
INTENT_ASSET_HISTORY = "asset_history"
INTENT_MAINTENANCE_LIST = "maintenance_list"
INTENT_HANDOVER_LIST = "handover_list"
INTENT_DAMAGE_LIST = "damage_list"
INTENT_REQUEST_STATUS = "request_status"
INTENT_HUMAN_AGENT = "human_agent"
INTENT_UNKNOWN = "unknown"

ALL_INTENTS = [
    INTENT_GREETING, INTENT_THANKS, INTENT_GOODBYE, INTENT_HELP,
    INTENT_IDENTITY, INTENT_CREATOR, INTENT_UNIT_DETAIL, INTENT_ASSET_TOP,
    INTENT_RECAP, INTENT_CHECK_STOCK, INTENT_ASSET_SEARCH, INTENT_ASSET_DETAIL,
    INTENT_ASSET_USER, INTENT_ASSET_HISTORY, INTENT_MAINTENANCE_LIST,
    INTENT_HANDOVER_LIST, INTENT_DAMAGE_LIST, INTENT_REQUEST_STATUS,
    INTENT_HUMAN_AGENT, INTENT_UNKNOWN,
]

CAT_FACTUAL = "factual"
CAT_GENERAL = "general"
CAT_OTHER = "other"

INTENT_CATEGORY = {
    INTENT_CHECK_STOCK: CAT_FACTUAL,
    INTENT_ASSET_USER: CAT_FACTUAL,
    INTENT_ASSET_HISTORY: CAT_FACTUAL,
    INTENT_GREETING: CAT_GENERAL,
    INTENT_THANKS: CAT_GENERAL,
    INTENT_GOODBYE: CAT_GENERAL,
    INTENT_HELP: CAT_GENERAL,
    INTENT_IDENTITY: CAT_GENERAL,
    INTENT_CREATOR: CAT_GENERAL,
}

DEFAULT_CONF_FACTUAL = 0.90
DEFAULT_CONF_GENERAL = 0.75
DEFAULT_CONF_OTHER = 0.80
DEFAULT_LOW_CONF_FLOOR = 0.70


def get_intent_category(intent):
    """Satu-satunya penentu kategori intent (cermin GetIntentCategory)."""
    return INTENT_CATEGORY.get(intent, CAT_OTHER)


def confidence_threshold(intent, configured=0.0):
    """Nilai konfigurasi <= 0 berarti 'belum diisi' → default kategori.

    Konfigurasi nol tidak boleh mematikan guardrail (cermin WACS).
    """
    if configured and configured > 0:
        return configured
    cat = get_intent_category(intent)
    if cat == CAT_FACTUAL:
        return DEFAULT_CONF_FACTUAL
    if cat == CAT_GENERAL:
        return DEFAULT_CONF_GENERAL
    return DEFAULT_CONF_OTHER


def low_confidence_floor(intent, configured=0.0):
    """Floor handover selalu dijepit ke threshold kategori (cermin WACS)."""
    threshold = confidence_threshold(intent, configured)
    return min(DEFAULT_LOW_CONF_FLOOR, threshold)


def is_valid_intent(name):
    return name in ALL_INTENTS


# ============================================================================
# 2. Normalisasi Bahasa Indonesia (cermin NormalizeIndonesian WACS)
# ============================================================================

# INVARIAN (diadopsi dari WACS): value tidak boleh memuat key yang diterapkan
# sesudahnya, karena tiap entri hanya berjalan sekali di seluruh string.
_SLANG_TABLE = {
    # singkatan umum chat
    "brp": "berapa", "brapa": "berapa", "brpa": "berapa",
    "gmn": "bagaimana", "gmna": "bagaimana", "gimana": "bagaimana",
    "knp": "kenapa", "kpn": "kapan", "dgn": "dengan", "utk": "untuk",
    "bgt": "banget", "kalo": "kalau", "klo": "kalau", "karna": "karena",
    "krn": "karena", "jgn": "jangan", "bsa": "bisa", "emg": "memang",
    "skrg": "sekarang", "sy": "saya",
    "ga": "tidak", "gak": "tidak", "nggak": "tidak", "tdk": "tidak",
    "yg": "yang",
    "sdh": "sudah", "udh": "sudah", "udah": "sudah", "blm": "belum",
    "dapet": "dapat", "sampe": "sampai", "nyampe": "sampai",
    "hlo": "halo", "hllo": "halo", "halllo": "halo",
    "develo": "developer", "develop": "developer",
    "thx": "terima kasih", "makasi": "terima kasih",
    "makasih": "terima kasih", "mksh": "terima kasih", "tengkyu": "terima kasih",
    "met pagi": "selamat pagi", "met siang": "selamat siang",
    "met sore": "selamat sore", "met malem": "selamat malam",
    # kata kerja tanya ala chat
    "liat": "lihat", "kasi": "kasih", "kasitau": "kasih tahu",
    "coba": "tolong", "adakah": "apakah ada",
    # ejaan stok / barang
    "stock": "stok", "stokc": "stok", "stocknya": "stok nya", "stoknya": "stok nya",
    "stok na": "stok nya", "abis": "habis", "sold out": "habis",
    "rdy": "ready", "redy": "ready", "brang": "barang",
    "prduk": "produk", "tnya": "tanya", "asett": "aset",
    "free": "tersedia",
    "availabe": "available", "avaiable": "available",
    "avalable": "available", "availabel": "available",
    # ejaan consumable lapangan (OP-2 + IT-5)
    "daptor": "adaptor", "conector": "konektor", "connector": "konektor",
    "antene": "antena", "breket": "bracket", "bracketnya": "bracket nya",
    "sikring": "sekring", "solasi": "isolasi", "koaksial": "coaxial",
    # ejaan aset IT
    "lptop": "laptop", "leptop": "laptop", "print": "printer",
    "pritner": "printer", "printernya": "printer nya",
    "kmputer": "komputer", "komptr": "komputer",
    "mnitor": "monitor", "monitr": "monitor", "mose": "mouse",
    "kyboard": "keyboard", "keybord": "keyboard",
    "srial": "serial", "seryal": "serial",
}

# Urutan terpanjang-dulu agar frasa menang atas katanya (cermin WACS).
_SLANG_KEYS = sorted(_SLANG_TABLE, key=lambda k: (-len(k), k))
_SLANG_PATTERNS = [
    (re.compile(r"\b" + re.escape(k) + r"\b"), _SLANG_TABLE[k])
    for k in _SLANG_KEYS
]
_CLEAN_PATTERN = re.compile(r"[^a-zA-Z0-9\s-]")


def normalize_id(text):
    """Lowercase + lipat slang chat + buang tanda baca (cermin WACS)."""
    s = (text or "").lower()
    for pattern, replacement in _SLANG_PATTERNS:
        s = pattern.sub(replacement, s)
    s = _CLEAN_PATTERN.sub(" ", s)
    return " ".join(s.split())


def tokens_of(text):
    """Tokenisasi batas-kata (agar 'ga' tak ditemukan di dalam 'juga')."""
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _has_token(tokens, want):
    return want in set(tokens)


def _has_stem(tokens, stems):
    for tok in tokens:
        for stem in stems:
            if stem in tok:
                return True
    return False


# ============================================================================
# 3. L2 override deterministik (cermin CatalogQueryOverride WACS)
# ============================================================================

# Topik non-IT: pertanyaan berbentuk katalog ("ada ... gak") yang objeknya
# topik lain harus DITOLAK override agar tak dipaksa jadi pencarian aset.
_NON_IT_TOPIC_STEMS = [
    "gaji", "cuti", "absen", "lembur", "kontrak", "sewa", "catering",
    "makan", "transport", "bensin", "solar", "promo", "diskon",
]
_RE_ASSET_TAG = re.compile(r"\b([A-Z]{2,}[A-Z0-9]*-?[0-9][A-Z0-9/-]*)\b")
# V2 (goals2.md §9): kode berantakan ala lapangan — spasi/titik/strip/underscore
# sama saja: "DT 02", "DT.02", "DT_02", "DT-02", "ITLT 002".
# Regex split ini menangkap PREFIX + pemisah + ANGKA, mis. "ITLT 002" -> ITLT-002.
_RE_SPLIT_REF = re.compile(r"\b([A-Z]{2,})\s*[.\-_\s]+\s*(0*[0-9][A-Z0-9]*)\b")
# Alias lapangan -> kategori kanonik (master_data.xml: Dump Truck, Water Truck,
# Excavator, Light Vehicle (LV); Type Barang.md: DT/EX/LV/WT + dozer/grader).
_FLEET_ALIASES = {
    "exca": "excavator", "excavator": "excavator", "beko": "excavator",
    "dt": "dump truck", "dump truck": "dump truck", "dumptruck": "dump truck",
    "wt": "water truck", "water truck": "water truck", "watertruck": "water truck",
    "lv": "light vehicle", "light vehicle": "light vehicle",
    "dozer": "dozer", "grader": "grader",
}
def _stripped_ref(ref):
    """Kanonik perbandingan: buang semua non-alnum, uppercase. 'DT-02'->'DT02'."""
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


def canonical_asset_ref(prefix, num):
    """'dt'+'02' -> 'DT-02'. Num dipertahankan apa adanya (nol depan ikut)."""
    return "%s-%s" % ((prefix or "").upper(), (num or "").upper())


def ref_variants(ref):
    """Kembalikan varian pencarian untuk satu ref kanonik/strip.

    Mis. 'DT-02' -> ['DT-02','DT02','DT 02','DT.02','DT-2','DT-02','DT-002',...].
    Dipakai backend untuk OR ilike agar 'dt 02' ketemu 'DT-02' di DB.
    """
    up = (ref or "").upper().strip()
    m = re.match(r"^([A-Z]{2,})[^A-Z0-9]*([0-9]+)([A-Z0-9/-]*)$", up)
    if not m:
        return [up] if up else []
    prefix, digits, tail = m.group(1), m.group(2), (m.group(3) or "")
    try:
        n = int(re.match(r"[0-9]+", digits).group(0))
    except (AttributeError, ValueError):
        n = None
    nums = {digits}
    if n is not None:
        nums.add(str(n))
        nums.add("%02d" % n)
        nums.add("%03d" % n)
    outs = []
    for num in sorted(nums):
        full = num + tail
        for form in ("%s-%s" % (prefix, full), "%s%s" % (prefix, full),
                     "%s %s" % (prefix, full), "%s.%s" % (prefix, full)):
            if form not in outs:
                outs.append(form)
    if up not in outs:
        outs.insert(0, up)
    return outs


def _iter_refs_in(text):
    """Yield ref kanonik dari teks (rapat + split), dedup by stripped form."""
    up = (text or "").upper()
    seen = set()
    for match in _RE_ASSET_TAG.finditer(up):
        ref = match.group(1)
        if not any(ch.isdigit() for ch in ref):
            continue
        key = _stripped_ref(ref)
        if key and key not in seen:
            seen.add(key)
            yield ref
    for match in _RE_SPLIT_REF.finditer(up):
        prefix, num = match.group(1), match.group(2)
        if not any(ch.isdigit() for ch in num):
            continue
        ref = canonical_asset_ref(prefix, num)
        key = _stripped_ref(ref)
        if key and key not in seen:
            seen.add(key)
            yield ref


def _has_any_ref(text):
    for _r in _iter_refs_in(text):
        return True
    return False


def resolve_fleet_alias(text):
    """Kembalikan kategori kanonik bila teks memuat alias fleet ('exca'->'excavator')."""
    norm = normalize_id(text)
    # frasa panjang dulu agar 'dump truck' menang atas 'dt' lepas
    for alias in sorted(_FLEET_ALIASES, key=lambda k: (-len(k), k)):
        if re.search(r"\b" + re.escape(alias) + r"\b", norm):
            return _FLEET_ALIASES[alias]
    return ""


# HT vs Rig: keduanya berkategori "Radio Rig" di master_data, dibedakan lewat
# nama/model (backend: filter name/product ilike). Rig dicek dulu.
_RE_RADIO_RIG = re.compile(r"\b(radio\s+rig|rig)\b", re.I)
_RE_RADIO_HT = re.compile(r"\b(radio\s+ht|ht|handy\s+talky|handy)\b", re.I)


def resolve_radio_kind(text):
    """'rig' | 'ht' | '' — jenis radio genggam vs rig."""
    t = text or ""
    if _RE_RADIO_RIG.search(t):
        return "rig"
    if _RE_RADIO_HT.search(t):
        return "ht"
    return ""


# IT vs Operasional (it_asset.asset.asset_type). "it" hanya dihitung bila
# digandeng kata benda ("aset it") agar "itu/sulit" tak ikut kena.
_RE_ASSET_TYPE_OP = re.compile(
    r"\b(operasional|operational|operation|operasi)\b", re.I)
_RE_ASSET_TYPE_IT = re.compile(
    r"\b(aset|asset|barang|unit|alat|tipe|type)\s+it\b", re.I)


def resolve_asset_type(text):
    """'it' | 'operation' | '' — domain pengelolaan aset."""
    t = text or ""
    if _RE_ASSET_TYPE_OP.search(t):
        return "operation"
    if _RE_ASSET_TYPE_IT.search(t):
        return "it"
    return ""
_RE_STOCK_WORD = re.compile(r"\b(stok|ready|tersedia|sisa|tersisa|menipis|habis|restock|minimum|consumable)\b", re.I)
_RE_RECAP_WORD = re.compile(r"\b(rekap|ringkas|total aset|jumlah aset|statistik)\b", re.I)
_RE_HISTORY_WORD = re.compile(r"\b(riwayat|histor[yi]|history)\b", re.I)


# Form: jenis, status, periode (goals2.md §13). Status "belum ..." selalu
# menang atas kata spesifik ("belum fulfilled" -> open, bukan fulfilled).
_FORM_KIND_PATTERNS = [
    ("handover", [r"\bserah\s*terima\b", r"\bhandover\b", r"\bbast\b", r"\bfstb\b"]),
    ("material", [r"\bmaterial\s*request\b", r"\bpengajuan\s+(barang|material)\b",
                  r"\bpermintaan\s+(barang|material)\b"]),
    ("asset_request", [r"\basset\s*request\b", r"\bpermintaan\s*aset\b",
                       r"\bpengajuan\s*aset\b"]),
    ("account", [r"\baccount\s*request\b", r"\bpermintaan\s*akun\b",
                 r"\bpengajuan\s*akun\b", r"\bminta\s*akun\b"]),
]

_FORM_STATUS_OPEN = [r"\bbelum\b", r"\bpending\b", r"\bmenunggu\b",
                     r"\bproses\b", r"\bgantung\b"]
_FORM_STATUS_SPECIFIC = [
    ("rejected", [r"\bditolak\b", r"\brejected\b", r"\btolak\b"]),
    ("approved", [r"\bdisetujui\b", r"\bapproved\b", r"\bsetuju\b",
                  r"\bacc\b"]),
    ("submitted", [r"\bdiajukan\b", r"\bsubmitted\b", r"\bdikirim\b"]),
    ("signed", [r"\bsigned\b", r"\bditandatangani\b", r"\bttd\b",
                r"\btanda\s*tangan\b"]),
    ("confirmed", [r"\bconfirmed\b", r"\bdikonfirmasi\b",
                   r"\bkonfirmasi\b"]),
    ("resolved", [r"\bresolved\b"]),
    ("fulfilled", [r"\bfulfilled\b", r"\bterpenuhi\b", r"\bfulfill\b"]),
    ("draft", [r"\bdraft\b", r"\bkonsep\b"]),
]
_FORM_STATUS_DONE = [r"\bsudah\b", r"\bselesai\b", r"\bdone\b",
                     r"\btuntas\b", r"\bberes\b"]

_PERIOD_PATTERNS = [
    ("today", [r"\bhari\s*ini\b"]),
    ("yesterday", [r"\bkemarin\b", r"\bkemaren\b"]),
    ("this_week", [r"\bminggu\s*ini\b", r"\bpekan\s*ini\b"]),
    ("this_month", [r"\bbulan\s*ini\b"]),
    ("last_month", [r"\bbulan\s*(lalu|kemarin|kemaren)\b"]),
]

_TOP_KIND_PATTERNS = [
    ("moved", [r"\bpindah\b", r"\bmutasi\b", r"\bgonta\s*ganti\b"]),
    ("damaged", [r"\brusak\b", r"\bbroken\b"]),
    ("oldest", [r"\btua\b", r"\blama\b", r"\bawal\b", r"\bpertama\b"]),
    ("newest", [r"\bbaru\b", r"\bakhir\b", r"\bmuda\b", r"\banyar\b"]),
]


def resolve_top_kind(text):
    """'oldest' | 'newest' | 'moved' | 'damaged' | '' — jenis ranking."""
    t = text or ""
    for token, pats in _TOP_KIND_PATTERNS:
        for p in pats:
            if re.search(p, t, re.I):
                return token
    return ""


def resolve_form_kind(text):
    t = text or ""
    for kind, pats in _FORM_KIND_PATTERNS:
        for p in pats:
            if re.search(p, t, re.I):
                return kind
    return ""


def resolve_form_status(text):
    """draft/submitted/approved/fulfilled/rejected/signed/confirmed/resolved,
    atau grup 'open' (belum) / 'done' (sudah)."""
    t = text or ""
    for p in _FORM_STATUS_OPEN:
        if re.search(p, t, re.I):
            return "open"
    for token, pats in _FORM_STATUS_SPECIFIC:
        for p in pats:
            if re.search(p, t, re.I):
                return token
    for p in _FORM_STATUS_DONE:
        if re.search(p, t, re.I):
            return "done"
    return ""


def resolve_period(text):
    t = text or ""
    for token, pats in _PERIOD_PATTERNS:
        for p in pats:
            if re.search(p, t, re.I):
                return token
    return ""


def _names_non_it_topic(raw_text):
    return _has_stem(tokens_of(raw_text), _NON_IT_TOPIC_STEMS)


# Prefix fleet sesuai master it_asset.unit.category project
# (Dump Truck, Water Truck, Excavator, Light Vehicle). Fleet = operasional,
# bukan IT. IT vs Operasional sendiri DITENTUKAN field DB asset_type
# (it/operation), bukan tebakan kode — kode hanya untuk ekstraksi ref.
FLEET_PREFIXES = ("DT", "EX", "LV", "WT")
_FLEET_RE = re.compile(r"^(%s)\D*\d" % "|".join(FLEET_PREFIXES))


def _is_fleet_ref(ref):
    """True bila kode aset/unit berawalan fleet (DT/EX/LV/WT)."""
    return bool(_FLEET_RE.match((ref or "").upper()))


def asset_query_override(raw_text):
    """Sinyal kuat yang memaksa intent + confidence 1.0 (cermin WACS L2).

    Mengembalikan nama intent atau None. Hanya mengklaim objek IT.
    """
    if _names_non_it_topic(raw_text):
        return None
    if _has_any_ref(raw_text):
        # Kode seperti ITLT-007 / PRN-01 / ITLT-002 / DT-02 selalu merujuk aset
        # spesifik — termasuk varian berantakan "itlt02", "DT 02", "dt.02".
        if _RE_HISTORY_WORD.search(raw_text):
            return INTENT_ASSET_HISTORY
        if re.search(r"\b(siapa|pakai|pengguna|pemakai|milik|punya)\b", raw_text, re.I):
            return INTENT_ASSET_USER
        for ref in _iter_refs_in(raw_text):
            if _is_fleet_ref(ref):
                return INTENT_UNIT_DETAIL
        return INTENT_ASSET_DETAIL
    if _RE_STOCK_WORD.search(raw_text):
        return INTENT_CHECK_STOCK
    if _RE_RECAP_WORD.search(raw_text):
        return INTENT_RECAP
    if _RE_HISTORY_WORD.search(raw_text):
        return INTENT_ASSET_HISTORY
    return None


# ============================================================================
# 4. Layer 1c — rule luar-domain eksplisit (cermin ood.go WACS)
# ============================================================================

OOD_WEATHER = "weather"
OOD_MATH = "math"
OOD_CLOCK = "clock_time"
OOD_IDENTITY = "self_identity"
OOD_PERSONAL = "personal"
OOD_TRIVIA = "general_trivia"

# Kata benda in-domain: pertanyaan yang memuatnya BUKAN pertanyaan jam/umum,
# mis. "jam berapa maintenance server" tetap soal IT.
_IN_DOMAIN_NOUNS = [
    "aset", "asset", "stok", "laptop", "printer", "radio", "komputer",
    "server", "maintenance", "handover", "request", "inventaris",
    "serial", "pengguna", "riwayat", "gudang", "it ", " Sparepart",
    "consumable", "toner", "tinta", "jaringan", "wifi",
]

_RE_WEATHER = re.compile(r"\b(cuaca|hujan|berangin|salju|mendung|prakiraan|bmkg|iklim|badai|banjir|gempa|kabut|panas|dingin)\b", re.I)
# F3: kata langit (langit/biru/bintang/...) hanya OOD bila TANPA kata benda
# in-domain ("kenapa langit warna biru" -> OOD, "cctv biru" tetap inventaris).
# "bulan" SENGAJA dikecualikan agar "handover bulan ini" tak rusak.
_RE_SKY = re.compile(r"\b(langit|biru|bintang|matahari|pelangi|angkasa|awan)\b", re.I)
_RE_MATH = re.compile(r"(\d+\s*[+\-*/x×÷]\s*\d+|\b(hitung|hitunglah|kalkulator|rumus|persamaan|matematika|tambah|kurang|kali|bagi)\b)", re.I)
_RE_CLOCK_PHRASE = re.compile(r"\b(jam|pukul)\b", re.I)
_RE_CLOCK_QUAL = re.compile(r"\b(berapa|sekarang)\b", re.I)
_RE_IDENTITY = re.compile(r"(\b(kamu|anda|kalian)\b.{0,25}\b(siapa|bot|robot|manusia|pintar)\b|\bsiapa\b.{0,25}\b(kamu|anda)\b|\bai\b)", re.I)
_RE_PERSONAL = re.compile(r"\b(kamu|anda)\b.{0,25}\b(pacar|umur|tinggal|rasa|suka|cinta|rindu|sedih|senang|sayang|nikah|gaji)\b", re.I)
_RE_TRIVIA = re.compile(r"\b(resep|film|lagu|musik|berita|presiden|pemilu|zodiak|horoskop|saham|terjemah(kan|an)?|translate|kamus|sinonim|antonim|definisi|sepak bola|skor)\b", re.I)


def _has_domain_noun(lower):
    """True bila ada kata benda in-domain di teks.

    Kata pendek (<=3 huruf, mis. 'it') wajib cocok batas-kata penuh agar
    'langit' tak terbaca mengandung 'it'.
    """
    for noun in _IN_DOMAIN_NOUNS:
        n = (noun or "").strip()
        if not n:
            continue
        if len(n) <= 3:
            if re.search(r"\b" + re.escape(n) + r"\b", lower):
                return True
        elif n in lower:
            return True
    return False


def _matches_clock(text):
    if not (_RE_CLOCK_PHRASE.search(text) and _RE_CLOCK_QUAL.search(text)):
        return False
    return not _has_domain_noun(text.lower())


def _matches_sky(text):
    if not _RE_SKY.search(text):
        return False
    return not _has_domain_noun(text.lower())


def ood_match(raw_text):
    """(reason, True) bila teks dikenali di luar domain IT, cermin OODMatch."""
    trimmed = (raw_text or "").strip()
    if not trimmed:
        return "", False
    if _RE_WEATHER.search(trimmed):
        return OOD_WEATHER, True
    if _matches_sky(trimmed):
        return OOD_WEATHER, True
    if _RE_MATH.search(trimmed):
        return OOD_MATH, True
    if _matches_clock(trimmed):
        return OOD_CLOCK, True
    if _RE_IDENTITY.search(trimmed):
        return OOD_IDENTITY, True
    if _RE_PERSONAL.search(trimmed):
        return OOD_PERSONAL, True
    if _RE_TRIVIA.search(trimmed):
        return OOD_TRIVIA, True
    return "", False


# ============================================================================
# 5. Layer 1 — rule deterministik (cermin defaultRules WACS, domain IT)
# ============================================================================

_RULES = [
    # --- human agent (paling eksplisit dulu) ---
    (re.compile(r"\b(bicara|sambung(kan)?|hubungi|hubungkan|mau ngomong|connect|chat)\s+(sama\s+|dengan\s+|ke\s+)?(admin|cs|customer service|teknisi|staff it|orang it|manusia)\b", re.I), INTENT_HUMAN_AGENT, 0.98),
    (re.compile(r"^(admin|cs|teknisi|halo admin|panggil (admin|teknisi))$", re.I), INTENT_HUMAN_AGENT, 0.96),
    # --- sapaan ---
    (re.compile(r"^(halo|hai|hi|hey|hei|helo|hello|hallo|selamat\s+(pagi|siang|sore|malam)|assalamu'?alaikum|waalaikumsalam)\b", re.I), INTENT_GREETING, 0.95),
    (re.compile(r"^(apa\s+kabar|bagaimana\s+kabar)", re.I), INTENT_GREETING, 0.94),
    # --- terima kasih & pamit ---
    (re.compile(r"\b(terima\s*kasih|makasih|thanks|matur nuwun|suwun)\b", re.I), INTENT_THANKS, 0.96),
    (re.compile(r"\b(selamat tinggal|bye|sampai jumpa|dadah)\b", re.I), INTENT_GOODBYE, 0.95),
    # --- identitas & pembuat (sebelum help/OOD agar "kamu siapa" terjawab) ---
    (re.compile(r"\b(kamu\s+siapa|siapa\s+(kamu|anda)|nama\s*kamu|namamu|fungsi\s*mu|fungsimu|apa\s*fungsimu|tugas\s*mu|tugasmu|peran\s*mu)\b", re.I), INTENT_IDENTITY, 0.95),
    (re.compile(r"\b(siapa\s+yang\s+(buat|membuat|bikin|menciptakan|mengembangkan)|dibuat\s+(oleh\s+)?siapa|pembuat(nya)?|developer(nya)?|pencipta)\b", re.I), INTENT_CREATOR, 0.95),
    # --- bantuan ---
    (re.compile(r"\b(bisa\s+apa|bantuan|contoh\s+pertanyaan|cara\s+pakai|help|menu\s+apa)\b", re.I), INTENT_HELP, 0.94),
    # --- rekap ---
    (re.compile(r"\b(rekap|ringkas(an)?|total\s+aset|jumlah\s+aset|statistik|dashboard)\b", re.I), INTENT_RECAP, 0.94),
    # --- stok ---
    (re.compile(r"\b(stok|stock|sisa|tersisa|menipis|habis|restock|kekurangan\s+stok|minimum|consumable)\b", re.I), INTENT_CHECK_STOCK, 0.94),
    # --- superlatif (sebelum riwayat agar "riwayat paling banyak pindah"
    # tidak jatuh ke riwayat generik) ---
    (re.compile(r"\b(paling|terbanyak|tersering|paling\s+banyak|sering)\b.{0,20}\b(tua|lama|baru|pindah|mutasi|rusak|servis)\b", re.I), INTENT_ASSET_TOP, 0.90),
    (re.compile(r"\b(tertua|terbaru|terlama|termuda)\b", re.I), INTENT_ASSET_TOP, 0.90),
    # --- riwayat servis = maintenance (spesifik menang atas umum) ---
    (re.compile(r"\briwayat\s+(servis|service|perbaikan|maintenance)\b", re.I), INTENT_MAINTENANCE_LIST, 0.94),
    (re.compile(r"\bpernah\b.{0,30}\b(di?pegang|dipakai|dimiliki)\b", re.I), INTENT_ASSET_HISTORY, 0.92),
    # --- riwayat ---
    (re.compile(r"\b(riwayat|histor[yi]|history|track\s+record)\b", re.I), INTENT_ASSET_HISTORY, 0.94),
    # --- pengguna ---
    (re.compile(r"\b(siapa\s+(yang\s+)?(pakai|memakai|pegang)|dipakai\s+(oleh|siapa)|pengguna|pemakai|dipegang|milik|punya|asetnya|user\s+nya)\b", re.I), INTENT_ASSET_USER, 0.94),
    # --- domain IT vs Operasional (setelah pengguna agar "siapa pakai aset
    # operasional" tetap ke asset_user) ---
    (re.compile(r"\b(aset|asset|barang)\s+it\b|\boperasional\b|\boperational\b|\boperation\b|\boperasi\b", re.I), INTENT_ASSET_SEARCH, 0.90),
    # --- unit/fleet: kode DT/EX/LV/WT selalu soal unit (paling dulu, bahkan
    # sebelum aturan tag umum yang case-sensitive) ---
    (re.compile(r"\b(DT|EX|LV|WT)\s*[.\-_]\s*[0-9]", re.I), INTENT_UNIT_DETAIL, 0.92),
    (re.compile(r"\b(DT|EX|LV|WT)\s+[0-9]", re.I), INTENT_UNIT_DETAIL, 0.92),
    (re.compile(r"\b(DT|EX|LV|WT)0*[0-9]", re.I), INTENT_UNIT_DETAIL, 0.90),
    # --- kode tag aset mentah (rapat: ITLT-007 / ITLT02) ---
    (re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-?[0-9][A-Z0-9/-]*\b"), INTENT_ASSET_DETAIL, 0.92),
    # --- info unit: merek/status/aset terpasang ---
    (re.compile(r"\b(merek|merk|brand|model\s+unit|modelnya|status\s+unit|aset\s+terpasang|radio\s+terpasang|terpasang|dipasang|unit\s+(ini|tersebut))\b", re.I), INTENT_UNIT_DETAIL, 0.90),
    # --- V2: kode split berantakan (DT 02 / DT.02 / ITLT 002 / DT_02) ---
    (re.compile(r"\b[A-Z]{2,}\s*[.\-_]\s*[0-9]", re.I), INTENT_ASSET_DETAIL, 0.92),
    (re.compile(r"\b[A-Z]{2,}\s+[0-9]", re.I), INTENT_ASSET_DETAIL, 0.92),
    # --- detail / kondisi spesifik ---
    (re.compile(r"\b(detail|spesifikasi|spek|cari(kan)?|informasi|kondisi)\b.{0,30}\b(aset|asset|laptop|printer|radio|komputer|serial|tag)\b", re.I), INTENT_ASSET_DETAIL, 0.93),
    (re.compile(r"\b(aset|asset|laptop|printer|radio|komputer|serial|tag)\b.{0,30}\b(detail|spesifikasi|spek|kondisi|dimana|di mana)\b", re.I), INTENT_ASSET_DETAIL, 0.93),
    (re.compile(r"\bkondisi\b", re.I), INTENT_ASSET_DETAIL, 0.90),
    # --- kata info: tipe/kategori -> daftar; spek -> detail per aset ---
    (re.compile(r"\b(tipe|type|jenis|kategori|category)\b", re.I), INTENT_ASSET_SEARCH, 0.88),
    (re.compile(r"\b(spek|spesifikasi)\b", re.I), INTENT_ASSET_DETAIL, 0.88),
    # --- damage report ---
    (re.compile(r"\b(damage(\s*report)?|laporan\s+kerusakan|berita\s+acara|form\s+kerusakan|barang\s+hilang|hilang)\b", re.I), INTENT_DAMAGE_LIST, 0.93),
    # --- maintenance ---
    (re.compile(r"\b(maintenance|perbaikan|servis|service|pernah\s+diservis|riwayat\s+servis|biaya\s+servis)\b", re.I), INTENT_MAINTENANCE_LIST, 0.93),
    # --- handover ---
    (re.compile(r"\b(handover|serah\s+terima|fstb|bast)\b", re.I), INTENT_HANDOVER_LIST, 0.93),
    # --- request ---
    (re.compile(r"\b(request|permintaan|pengajuan|material\s+request|asset\s+request)\b.{0,30}\b(status|terakhir|pending|disetujui|ditolak|daftar|list)\b", re.I), INTENT_REQUEST_STATUS, 0.93),
    (re.compile(r"\b(status|daftar|list)\b.{0,30}\b(request|permintaan|pengajuan)\b", re.I), INTENT_REQUEST_STATUS, 0.93),
    # --- jenis form pengajuan (tanpa kata status pun jelas) ---
    (re.compile(r"\b(material\s*request|pengajuan\s+(barang|material)|permintaan\s+(barang|material))\b", re.I), INTENT_REQUEST_STATUS, 0.90),
    (re.compile(r"\b(asset\s*request|permintaan\s*aset|pengajuan\s*aset)\b", re.I), INTENT_REQUEST_STATUS, 0.90),
    (re.compile(r"\b(account\s*request|permintaan\s*akun|pengajuan\s*akun|minta\s*akun)\b", re.I), INTENT_REQUEST_STATUS, 0.90),
    # --- pencarian / daftar aset (kondisi, status, kategori + fleet V2) ---
    (re.compile(r"\b(rusak|broken|degraded|lemot|tersedia|available|dipakai|digunakan|terpakai|in\s+use|out\s+of\s+service|retired|laptop|printer|radio|monitor|mouse|keyboard|komputer|server|cctv|desktop|gps|tablet|headset|proyektor|projector|router|switch|aset\s+apa|daftar\s+aset|list\s+aset)\b", re.I), INTENT_ASSET_SEARCH, 0.90),
    # --- kata consumable/material berdiri sendiri -> cek stok (OP-2 + IT-5).
    # Ditaruh setelah aturan aset agar "mouse rusak"/"radio ht" tetap ke aset.
    (re.compile(r"\b(konektor|adaptor|adapter|antena|bracket|fuse|sekring|isolasi|timah|flux|solder|coaxial|jumper|kabel|bnc|toner|tinta|kertas)\b", re.I), INTENT_CHECK_STOCK, 0.88),
    # --- V2: alias fleet/unit berdiri sendiri (exca / excavator / dump truck / dt / lv / wt / dozer / grader / fleet / unit) ---
    (re.compile(r"\b(exca|beko|excavator|dump\s*truck|dumptruck|water\s*truck|watertruck|light\s*vehicle|dozer|grader|fleet|unit|dt|lv|wt)\b", re.I), INTENT_ASSET_SEARCH, 0.88),
    # --- V2: jenis radio berdiri sendiri (ht / rig / handy talky) ---
    (re.compile(r"\b(radio\s*ht|radio\s*rig|ht|rig|handy\s*talky)\b", re.I), INTENT_ASSET_SEARCH, 0.88),
    # --- prefix tag tanpa angka (itct/itrg/itht -> daftar kategorinya) ---
    (re.compile(r"\b(itlt|itct|itrg|itht|itpr|prn)\b", re.I), INTENT_ASSET_SEARCH, 0.88),
]


def _apply_rules(text):
    for pattern, intent, conf in _RULES:
        if pattern.search(text):
            return {"intent": intent, "confidence": conf,
                    "category": get_intent_category(intent), "method": "rule",
                    "ood_reason": ""}
    return None


# ============================================================================
# 6. Layer 2 — TF-IDF + gate (cermin classifier.go WACS, angka disalin)
# ============================================================================

MIN_NLU_SIMILARITY = 0.30
NLU_WINNER_MARGIN = 1.4
CHAR_GRAM_WEIGHT = 0.35
CHAR_GRAM_MIN_WORD_LEN = 5
LONE_MATCH_FACTOR = 1.5
MIN_DISTINCTIVE_IDF = 3.8
NLU_CONF_SCALE = 1.15  # kalibrasi: skor × 1.15 (dibatasi 0.99), cermin WACS


def _featurize(text):
    """Unigram + bigram (bobot 1) + char 3-4gram kata ≥5 huruf (bobot 0.35)."""
    words = [w for w in normalize_id(text).split() if len(w) > 1]
    feats = []
    for w in words:
        feats.append((w, 1.0, False))
    for i in range(len(words) - 1):
        feats.append((words[i] + "_" + words[i + 1], 1.0, False))
    for w in words:
        if len(w) < CHAR_GRAM_MIN_WORD_LEN:
            continue
        padded = "^" + w + "$"
        for n in (3, 4):
            for i in range(len(padded) - n + 1):
                feats.append((padded[i:i + n], CHAR_GRAM_WEIGHT, True))
    return feats


# Exemplar per intent — kalimat dukungan NLU. ATURAN TETAP (cermin WACS):
# data evaluasi TIDAK BOLEH disalin ke sini; begitu melatih, ia berhenti
# mengukur generalisasi.
EXEMPLARS = [
    ("greeting", "halo kak"), ("greeting", "hai selamat pagi"),
    ("greeting", "halo min selamat pagi"),
    ("greeting", "assalamualaikum"), ("greeting", "halo apa kabar"),
    ("greeting", "selamat siang min"), ("greeting", "tes halo"),
    ("thanks", "terima kasih banyak"), ("thanks", "makasih infonya"),
    ("thanks", "oke makasih"),
    ("thanks", "ok terima kasih kak"), ("thanks", "suwun"),
    ("goodbye", "oke sampai jumpa"), ("goodbye", "dadah terima kasih"),
    ("goodbye", "bye kak"), ("goodbye", "cukup sekian dulu"),
    ("help", "kamu bisa bantu apa"), ("help", "contoh pertanyaan yang bisa ditanyakan"),
    ("help", "gimana cara pakai ask ai"), ("help", "fitur apa saja yang ada"),
    ("identity", "siapa namamu"), ("identity", "apa tugasmu"),
    ("identity", "kamu itu apa"), ("identity", "jelaskan peranmu"),
    ("creator", "siapa developer aplikasi ini"), ("creator", "siapa yang menciptakan kamu"),
    ("creator", "kamu dibuat siapa"), ("creator", "info pembuat bot ini"),
    ("unit_detail", "merek unit itu apa"), ("unit_detail", "spesifikasi fleet tersebut"),
    ("unit_detail", "status kendaraan tambang ini"), ("unit_detail", "radio apa saja di unit itu"),
    ("asset_top", "daftar aset tertua"), ("asset_top", "unit paling anyar"),
    ("asset_top", "aset yang kerap berpindah"), ("asset_top", "kerusakan paling sering"),
    ("recap", "rekap aset bulan ini"), ("recap", "ringkasan kondisi inventaris it"),
    ("recap", "total semua aset berapa"), ("recap", "jumlah aset tersedia dan dipakai"),
    ("recap", "statistik aset site wolo"), ("recap", "rekapitulasi aset dan stok"),
    ("check_stock", "stok radio ht berapa"), ("check_stock", "sisa kabel lan berapa"),
    ("check_stock", "liat sisa kabel dong"), ("check_stock", "kasih tahu radio ht yang masih ada"),
    ("check_stock", "stok mouse habis atau masih"), ("check_stock", "cek stok tinta printer"),
    ("check_stock", "stok consumable yang menipis"), ("check_stock", "barang apa yang perlu restock"),
    ("check_stock", "sisa kertas a4 di gudang"), ("check_stock", "stok keyboard tersisa berapa"),
    ("asset_search", "tampilkan laptop yang tersedia"), ("asset_search", "daftar printer yang rusak"),
    ("asset_search", "kasi info laptop yg tersedia"), ("asset_search", "coba tampilkan radio rusak"),
    ("asset_search", "aset broken apa saja"), ("asset_search", "radio yang sedang dipakai"),
    ("asset_search", "laptop degraded mana saja"), ("asset_search", "aset out of service ada berapa"),
    ("asset_search", "monitor yang belum dipakai"), ("asset_search", "daftar aset retired"),
    ("asset_detail", "detail laptop itu"), ("asset_detail", "spesifikasi komputer tersebut"),
    ("asset_detail", "info serial number aset ini"), ("asset_detail", "kondisi asetnya bagaimana"),
    ("asset_detail", "cari aset dengan tag tersebut"), ("asset_detail", "dimana posisi aset itu"),
    ("asset_user", "siapa yang memakai laptop ini"), ("asset_user", "pengguna printer itu siapa"),
    ("asset_user", "tolong cek siapa pemakai printer itu"),
    ("asset_user", "aset ini dipegang siapa"), ("asset_user", "laptopnya budi yang mana"),
    ("asset_user", "milik siapa radio tersebut"), ("asset_user", "user dari aset itu"),
    ("asset_history", "riwayat pemakaian laptop ini"), ("asset_history", "histori perpindahan aset tersebut"),
    ("asset_history", "minta histori aset tersebut"),
    ("asset_history", "pernah dipegang siapa saja"), ("asset_history", "history penempatan radio itu"),
    ("asset_history", "catatan peminjaman printer tersebut"),
    ("maintenance_list", "riwayat perbaikan printer"), ("maintenance_list", "biaya servis laptop kemarin"),
    ("maintenance_list", "jadwal maintenance server"), ("maintenance_list", "pernah diservis apa saja"),
    ("maintenance_list", "daftar perbaikan aset bulan ini"),
    ("handover_list", "daftar serah terima barang"), ("handover_list", "handover terakhir ke siapa"),
    ("handover_list", "dokumen fstb bulan lalu"), ("handover_list", "bast yang sudah ditandatangani"),
    ("handover_list", "bast kemarin untuk siapa"), ("handover_list", "serah terima minggu ini"),
    ("damage_list", "laporan kerusakan aset"), ("damage_list", "damage report yang belum resolved"),
    ("damage_list", "daftar barang hilang"), ("damage_list", "kerusakan yang dilaporkan minggu ini"),
    ("damage_list", "kerusakan yang sudah selesai"), ("damage_list", "laporan hilang bulan ini"),
    ("request_status", "status pengajuan barang saya"), ("request_status", "material request sudah disetujui belum"),
    ("request_status", "daftar permintaan aset"), ("request_status", "pengajuan laptop sampai mana"),
    ("request_status", "request yang masih pending"),
    ("request_status", "pengajuan material yang sudah terpenuhi"), ("request_status", "material request belum diproses"),
    ("request_status", "permintaan aset yang disetujui"), ("request_status", "status permintaan akun saya"),
    ("human_agent", "tolong hubungkan ke teknisi"), ("human_agent", "saya mau bicara dengan admin it"),
    ("human_agent", "minta nomor staff it"), ("human_agent", "butuh bantuan langsung dari orang it"),
]


class _Classifier:
    """TF-IDF cosine atas exemplar + tiga saringan gate (cermin WACS)."""

    def __init__(self, exemplars):
        self._exemplars = list(exemplars)
        doc_freq = {}
        vocab = set()
        for _intent, text in self._exemplars:
            seen = set()
            for feat, _w, _c in _featurize(text):
                vocab.add(feat)
                if feat not in seen:
                    doc_freq[feat] = doc_freq.get(feat, 0) + 1
                    seen.add(feat)
        self._vocab = {tok: i for i, tok in enumerate(vocab)}
        n_docs = float(len(self._exemplars))
        self._idf = {
            tok: math.log((n_docs + 1.0) / (df + 1.0)) + 1.0
            for tok, df in doc_freq.items()
        }
        self._ex_vecs = [self._vectorize(t) for _i, t in self._exemplars]

    def _vectorize(self, text):
        vec = [0.0] * len(self._vocab)
        tf = {}
        for feat, weight, _c in _featurize(text):
            tf[feat] = tf.get(feat, 0.0) + weight
        norm_sq = 0.0
        for feat, count in tf.items():
            idx = self._vocab.get(feat)
            if idx is None:
                continue
            val = count * self._idf[feat]
            vec[idx] = val
            norm_sq += val * val
        if norm_sq > 0:
            norm = math.sqrt(norm_sq)
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def _cosine(v1, v2):
        return sum(a * b for a, b in zip(v1, v2))

    def _max_word_idf(self, text):
        """IDF maksimum kata-bukan-gram yang dibagi teks & korpus (cermin WACS:
        char n-gram tidak dihitung agar tumpang-tindih gram tak mengecoh)."""
        words = {w for w in normalize_id(text).split() if len(w) > 1}
        best = 0.0
        for feat, _w, is_gram in _featurize(text):
            if is_gram:
                continue
            if feat in words or "_" in feat:
                best = max(best, self._idf.get(feat, 0.0))
        return best

    def classify_nlu(self, text):
        vec = self._vectorize(text)
        per_intent = {}
        for (intent, _t), evec in zip(self._exemplars, self._ex_vecs):
            score = self._cosine(vec, evec)
            if score > per_intent.get(intent, 0.0):
                per_intent[intent] = score
        ranked = sorted(per_intent.items(), key=lambda kv: kv[1], reverse=True)
        matched = [(i, s) for i, s in ranked if s > 0]
        if not matched:
            return None
        best_intent, best = matched[0]
        if best < MIN_NLU_SIMILARITY:
            return None
        if len(matched) == 1:
            if best < MIN_NLU_SIMILARITY * LONE_MATCH_FACTOR:
                return None
        else:
            second = matched[1][1]
            if second > 0 and best < second * NLU_WINNER_MARGIN:
                return None
            if second <= 0 and best < MIN_NLU_SIMILARITY * LONE_MATCH_FACTOR:
                return None
        if self._max_word_idf(text) < MIN_DISTINCTIVE_IDF:
            return None
        return {"intent": best_intent,
                "confidence": min(0.99, best * NLU_CONF_SCALE),
                "category": get_intent_category(best_intent),
                "method": "nlu", "ood_reason": ""}


_CLASSIFIER = _Classifier(EXEMPLARS)


def classify(raw_text):
    """Pipeline L1→L2→L3 (L1 LLM selalu menyerah bila nonaktif).

    Mengembalikan dict {intent, confidence, category, method, ood_reason}
    dengan method salah satu dari: rule, override, ood_rule, nlu, empty.
    """
    trimmed = (raw_text or "").strip()
    if not trimmed:
        return {"intent": INTENT_UNKNOWN, "confidence": 0.0,
                "category": CAT_OTHER, "method": "empty", "ood_reason": ""}
    # L1 nonaktif → langsung deterministik (cermin WACS saat Qwen mati).
    hit = _apply_rules(trimmed)
    if hit:
        return hit
    normalized = normalize_id(trimmed)
    if normalized != trimmed.lower().strip():
        hit = _apply_rules(normalized)
        if hit:
            return hit
    forced = asset_query_override(trimmed)
    if forced:
        return {"intent": forced, "confidence": 1.0,
                "category": get_intent_category(forced),
                "method": "override", "ood_reason": ""}
    reason, ok = ood_match(trimmed)
    if ok:
        return {"intent": INTENT_UNKNOWN, "confidence": 0.9,
                "category": CAT_OTHER, "method": "ood_rule",
                "ood_reason": reason}
    nlu = _CLASSIFIER.classify_nlu(trimmed)
    if nlu:
        return nlu
    return {"intent": INTENT_UNKNOWN, "confidence": 0.0,
            "category": CAT_OTHER, "method": "empty", "ood_reason": ""}


# ============================================================================
# L1 — Qwen Decide() (cermin internal/ai/agent/agent.go WACS)
#
# Qwen berjalan di runtime lokal (llama.cpp server + Qwen3-0.6B GGUF,
# endpoint OpenAI-compatible, default http://127.0.0.1:8081) — BUKAN di
# dalam Odoo. Modul ini hanya membangun prompt (dari ALL_INTENTS agar
# kosakata model tak bisa melenceng dari validasi runtime) dan memvalidasi
# JSON keputusan. Transport HTTP-nya disuntik dari ask_ai.py agar file ini
# tetap murni stdlib dan bisa diuji tanpa server.
#
# Kontrak keputusan (cermin Decision WACS): model HANYA mengeluarkan
# {intent, entities, constraints, confidence} — model tidak pernah memilih
# tool, tidak menyentuh database, dan tidak menyusun jawaban akhir.
# Kalimat jawaban tetap dibangun tool backend dari evidence ORM.
# ============================================================================

LLM_ENABLED = False  # dinyalakan via System Parameter it_asset.ask_ai.llm_enabled
LLM_MODEL_DEFAULT = "qwen3-0.6b"
LLM_MAX_DECISION_TOKENS = 192  # cermin maxDecisionTokens WACS
LLM_DECIDE_TEMPERATURE = 0.1   # keputusan: deterministik (cermin WACS)

# Kunci tertutup — model hanya boleh mengisi ini (cermin Decision.Constraints
# WACS: channel filter tak bisa diselundupkan lewat field entitas).
_DECISION_ENTITY_KEYS = ("asset_ref", "item", "employee", "category")
_DECISION_CONSTRAINT_KEYS = ("low_only",)

_DECISION_FEW_SHOTS = [
    ("stok radio ht berapa?", "check_stock", 0.9),
    ("rekap aset bulan ini", "recap", 0.9),
    ("siapa yang pakai ITLT-007", "asset_user", 0.9),
    ("riwayat printer PRN-01", "asset_history", 0.9),
    ("kamu siapa", "identity", 0.9),
    ("siapa yang buat kamu", "creator", 0.9),
]


def decision_system_prompt():
    """Prompt Decide(): daftar intent SELALU dari ALL_INTENTS (cermin WACS:
    menambah intent otomatis muncul di prompt tanpa berkas kedua disunting)."""
    lines = [
        "Kamu adalah penebak maksud pertanyaan inventaris IT berbahasa Indonesia.",
        "Balas HANYA dengan satu objek JSON:",
        '{"intent": "<salah satu intent di bawah>", '
        '"entities": {"asset_ref": "", "item": "", "employee": "", "category": ""}, '
        '"constraints": {"low_only": false}, "confidence": 0.0-1.0}',
        "Aturan: jangan mengarang kode aset/nama; isi entities hanya dari kata "
        "yang tertulis di pertanyaan; low_only=true hanya bila user menyebut "
        "menipis/habis/restock/minimum.",
        "Intent valid: " + ", ".join(ALL_INTENTS),
    ]
    for text, intent, conf in _DECISION_FEW_SHOTS:
        lines.append('Contoh: "%s" -> {"intent": "%s", "confidence": %s}' % (text, intent, conf))
    return "\n".join(lines)


def parse_decision(raw_str):
    """Validasi JSON keputusan model. None = tak terpakai → jatuh ke L3
    (cermin validasi IsValidIntent + validAction WACS)."""
    import json
    if not raw_str:
        return None
    start = raw_str.find("{")
    end = raw_str.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw_str[start:end + 1])
    except ValueError:
        return None
    intent = data.get("intent", "")
    if not is_valid_intent(intent) or intent == INTENT_UNKNOWN:
        return None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(1.0, conf))
    raw_ent = data.get("entities", {}) or {}
    entities = {k: str(raw_ent.get(k, "") or "")[:80]
                for k in _DECISION_ENTITY_KEYS}
    raw_con = data.get("constraints", {}) or {}
    constraints = {k: bool(raw_con.get(k, False))
                   for k in _DECISION_CONSTRAINT_KEYS}
    return {"intent": intent, "entities": entities,
            "constraints": constraints, "confidence": conf}


def llm_decide(raw_text, transport=None):
    """Jalankan L1 via transport yang disuntik.

    ``transport(system_prompt, user_text)`` mengembalikan string JSON mentah
    dari runtime Qwen (diimplementasikan di ask_ai.py dengan urllib stdlib).
    Tanpa transport (atau bila gagal/None) → None = L1 menyerah ke L2/L3,
    persis desain WACS saat runtime mati.
    """
    if not LLM_ENABLED or transport is None:
        return None
    try:
        raw = transport(decision_system_prompt(), raw_text)
    except Exception:
        return None
    return parse_decision(raw)


# ============================================================================
# 7. Entity extractor (cermin entity.Extract WACS; constraints kanal terpisah)
# ============================================================================

CATEGORY_GAZETTEER = [
    "laptop", "desktop", "printer", "radio rig", "radio ht", "radio",
    "rig", "monitor", "mouse", "keyboard", "pc",
    "komputer", "server", "router", "switch", "kabel", "proyektor",
    "projector", "cctv", "gps", "handy talky", "ht", "headset", "tablet",
    "toner", "tinta", "kertas", "flashdisk", "hardisk", "ssd", "ram",
    # V2 fleet/unit (master_data.xml + Type Barang.md OP-3)
    "excavator", "dump truck", "water truck", "light vehicle",
    "dozer", "grader", "fleet", "unit",
]

# Kanonik kategori: kunci harus persis cocok ("rig"->"Radio Rig" agar ilike
# DB ketemu; "ht"/"handy talky"->"radio" + radio_kind untuk filter nama).
_CATEGORY_CANONICAL = {
    "komputer": "pc",
    "radio rig": "Radio Rig",
    "rig": "Radio Rig",
    "radio ht": "radio",
    "handy talky": "radio",
    "ht": "radio",
}

# Prefix tag dari data asli (ITLT = laptop, ITCT = CCTV, ITRG = radio rig,
# ITHT = radio HT, PRN = printer): token prefix tanpa angka ikut menentukan
# kategori (+ jenis radio).
_TAG_PREFIX_CATEGORY = {
    "ITLT": "laptop",
    "ITCT": "cctv",
    "ITRG": "Radio Rig",
    "ITHT": "radio",
    "ITPR": "printer",
    "PRN": "printer",
}
_TAG_PREFIX_KIND = {
    "ITRG": "rig",
    "ITHT": "ht",
}

STATE_KEYWORDS = {
    "available": ["tersedia", "available", "ready", "siap pakai", "siap dipakai",
                  "belum dipakai", "nganggur", "kosong"],
    "in_use": ["dipakai", "digunakan", "terpakai", "in use", "sedang dipakai",
               "ter-assign"],
    "maintenance": ["maintenance", "out of service", "servis", "perbaikan",
                    "tidak bisa dipakai"],
    "retired": ["retired", "pensiun", "tidak aktif", "dihapus"],
}

CONDITION_KEYWORDS = {
    "broken": ["rusak", "broken", "mati total", "pecah", "hancur"],
    "degraded": ["degraded", "lemot", "lambat", "kurang bagus"],
    "good": ["bagus", "baik", "normal", "good"],
}

LOW_ONLY_STEMS = ["menipis", "habis", "restock", "minimum", "rendah",
                  "kosong", "dibawah", "kurang"]

_ITEM_STOPWORDS = {
    "ada", "apa", "saja", "aja", "yang", "yg", "stok", "stock", "sisa",
    "tersisa", "tersedia", "berapa", "berapakah", "cek", "tampilkan",
    "tampil", "lihat", "info", "informasi", "daftar", "list", "semua",
    "tolong", "mohon", "dong", "kah", "sih", "nya", "masih", "dan",
    "di", "berapa", "kasih", "tahu", "berapa?", "sisa?", "barang",
    "produk", "consumable", "gudang", "it", "untuk", "dengan", "dari",
    "berapa", "milik", "punya", "kondisi", "status", "detail", "riwayat",
    "siapa", "dimana", "mana", "cari", "ini", "itu", "tersebut", "saya",
    "kak", "min", "mas", "mbak", "pak", "bu", "pakai", "memakai", "pegang",
    "dipegang",
    # kata kerja tanya & benda umum jangan jadi keyword barang
    "cari", "carikan", "lihat", "liat", "tampilkan", "tampil",
    "tunjukkan", "tunjukin", "kasih", "kasi", "aset", "asset",
    # kata constraints jangan jadi keyword barang (kalau ikut, search miss)
    "menipis", "habis", "restock", "minimum", "rendah", "kosong", "dibawah",
}

_RE_EMPLOYEE_AFTER = re.compile(
    r"\b(dipakai oleh|dipegang oleh|milik|punya|asetnya|pemakai|pengguna|user)\b\s+([a-zA-Z][a-zA-Z .]{1,40})",
    re.I)


def extract_entities(raw_text):
    """Mengembalikan (entities, constraints) — dua kanal terpisah (cermin WACS).

    entities: asset_refs, category, radio_kind, asset_type, form_kind,
    form_status, period, top_kind, employee_name, item, state, condition.
    constraints: low_only (bool).
    """
    text = raw_text or ""
    norm = normalize_id(text)
    toks = norm.split()

    asset_refs = []
    for ref in _iter_refs_in(text):
        if ref not in asset_refs:
            asset_refs.append(ref)

    category = ""
    alias_cat = resolve_fleet_alias(text)
    if alias_cat:
        category = alias_cat
    else:
        # frasa terpanjang dulu agar "radio rig" menang atas "radio"
        for cat in sorted(CATEGORY_GAZETTEER, key=len, reverse=True):
            if cat in norm:
                category = _CATEGORY_CANONICAL.get(cat, cat)
                break
        if not category:
            # prefix tag tanpa angka ("itct" -> cctv, "itrg" -> rig)
            for tok in toks:
                up = tok.upper()
                if up in _TAG_PREFIX_CATEGORY and not any(
                        ch.isdigit() for ch in tok):
                    category = _TAG_PREFIX_CATEGORY[up]
                    break

    radio_kind = resolve_radio_kind(text)
    if not radio_kind:
        for tok in toks:
            up = tok.upper()
            if up in _TAG_PREFIX_KIND and not any(ch.isdigit() for ch in tok):
                radio_kind = _TAG_PREFIX_KIND[up]
                break
    asset_type = resolve_asset_type(text)
    form_kind = resolve_form_kind(text)
    form_status = resolve_form_status(text)
    period = resolve_period(text)
    top_kind = resolve_top_kind(text)

    employee_name = ""
    m = _RE_EMPLOYEE_AFTER.search(text)
    if m:
        name = re.sub(r"[?.,!]+$", "", m.group(2)).strip()
        # kupas ekor frasa tanya berulang ("... apa saja", "... saja dong")
        while True:
            shorter = re.sub(
                r"\b(apa saja|apa|aja|saja|dong|kah|ya|itu|ini|tersebut)$",
                "", name, flags=re.I).strip()
            if shorter == name:
                break
            name = shorter
        if len(name) >= 3:
            employee_name = name

    item = " ".join(w for w in toks if w not in _ITEM_STOPWORDS).strip()

    state = ""
    for key, words in STATE_KEYWORDS.items():
        if any(w in norm for w in words):
            state = key
            break
    condition = ""
    for key, words in CONDITION_KEYWORDS.items():
        if any(w in norm for w in words):
            condition = key
            break

    constraints = {"low_only": any(s in norm for s in LOW_ONLY_STEMS)}
    return ({"asset_refs": asset_refs, "category": category,
             "radio_kind": radio_kind, "asset_type": asset_type,
             "form_kind": form_kind, "form_status": form_status,
             "period": period, "top_kind": top_kind,
             "employee_name": employee_name, "item": item,
             "state": state, "condition": condition},
            constraints)


# ============================================================================
# 8. Scope reply bervariasi (cermin scope.go WACS — makna deterministik,
#    redaksi berotasi; topik disebut di level alasan, bukan echo kata user)
# ============================================================================

_SCOPE_POOLS = {
    OOD_WEATHER: [
        "Waduh, kalau soal cuaca saya kurang bisa bantu 🙏. Tapi untuk stok, aset, pengguna, dan riwayat IT, saya siap bantu.",
        "Info cuaca di luar jangkauan saya 😊. Saya fokus membantu data inventaris IT.",
        "Maaf, saya belum bisa bantu soal cuaca. Kalau ada pertanyaan aset atau stok IT, tanya saja ya.",
    ],
    OOD_MATH: [
        "Soal hitungan saya kurang bisa bantu 🙏. Kalau soal jumlah aset atau sisa stok, saya siap.",
        "Untuk perhitungan umum di luar jangkauan saya 😊. Saya lebih fokus ke data inventaris IT.",
        "Maaf, saya belum bisa bantu hitung-hitungan. Ada yang mau ditanyakan soal aset IT?",
    ],
    OOD_CLOCK: [
        "Soal jam di luar jangkauan saya 🙏. Kalau soal jadwal maintenance aset, itu bisa saya bantu.",
        "Maaf, saya tidak memantau waktu. Untuk riwayat maintenance aset, silakan tanya ke saya ya.",
        "Saya belum bisa bantu soal waktu 😊. Kalau soal data aset IT, saya siap.",
    ],
    OOD_IDENTITY: [
        "Saya asisten inventaris IT 😊, fokus membantu soal stok, aset, pengguna, dan riwayat.",
        "Saya bot Ask AI modul IT. Ada yang bisa saya bantu soal aset atau stok?",
        "Tugas saya membantu pertanyaan seputar inventaris IT 🙏. Silakan tanya soal stok, aset, atau riwayat.",
    ],
    OOD_PERSONAL: [
        "Waduh itu di luar keahlian saya 🙏. Saya fokus bantu soal data inventaris IT.",
        "Belum bisa jawab itu 😊, tapi kalau butuh info aset atau stok IT, tanya saja.",
        "Maaf, saya hanya bisa bantu seputar inventaris IT.",
    ],
    OOD_TRIVIA: [
        "Topik itu di luar layanan saya 🙏. Saya fokus membantu stok, aset, pengguna, dan riwayat IT.",
        "Maaf, saya hanya bisa bantu seputar inventaris IT. Ada pertanyaan aset atau stok?",
        "Belum bisa saya bantu topik itu 😊, tapi untuk data IT saya siap.",
    ],
}

_DEFAULT_SCOPE_POOL = [
    "Saya fokus membantu data inventaris IT: stok produk, jumlah aset, pengguna, kondisi, dan riwayat. Coba tuliskan pertanyaan yang berkaitan dengan itu ya.",
    "Pertanyaan itu di luar layanan saya 🙏. Saya bisa bantu soal stok, aset, pengguna, kondisi, atau riwayat IT.",
    "Belum bisa saya bantu untuk topik itu 😊. Kalau soal data inventaris IT, tanya saja ya.",
]

_DATA_MISS_POOL = [
    "Data tersebut belum tercatat di modul IT. Silakan hubungi staff IT untuk bantuan lebih lanjut.",
    "Maaf, data itu belum ada di inventaris IT 🙏. Untuk memastikannya, silakan hubungi staff IT ya.",
    "Belum tercatat di data IT. Coba kata kunci lain, atau hubungi staff IT untuk pengecekan langsung.",
]

_HANDOVER_POOL = [
    "Saya teruskan ke staff IT ya 🙏. Silakan hubungi tim IT Site Wolo langsung agar cepat ditangani.",
    "Untuk hal ini paling tepat dibantu staff IT langsung 😊. Silakan hubungi tim IT Site Wolo.",
    "Saya belum cukup yakin untuk menjawab itu — staff IT yang akan membantu lebih lanjut 🙏.",
]

_ROTATION = itertools.count()


def _fnv1a64(text):
    h = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def _pick_variant(seed, n):
    if n <= 1:
        return 0
    offset = _fnv1a64(seed) % n
    step = next(_ROTATION)
    return (offset + step) % n


def scope_reply(message, reason):
    pool = _SCOPE_POOLS.get(reason) or _DEFAULT_SCOPE_POOL
    return pool[_pick_variant(reason + "|" + message, len(pool))]


def data_miss_reply(message):
    return _DATA_MISS_POOL[_pick_variant(message, len(_DATA_MISS_POOL))]


def handover_reply(message):
    return _HANDOVER_POOL[_pick_variant(message, len(_HANDOVER_POOL))]


_CLARIFICATION_TEMPLATES = [
    "Bisa diperjelas %s? 🙏<br/>Contoh: <i>“stok radio ht”</i>, "
    "<i>“siapa yang pakai ITLT-007?”</i>, <i>“riwayat printer PRN-01”</i>.",
    "Hmm, kurang spesifik nih 😅 — %s yang mana ya?<br/>Contoh: "
    "<i>“stok radio ht”</i>, <i>“siapa yang pakai ITLT-007?”</i>.",
    "Biar tepat sasaran, sebutkan %s 🙏<br/>Contoh: "
    "<i>“rekap aset”</i>, <i>“riwayat printer PRN-01”</i>.",
]


def clarification_reply(intent, entities):
    """Klarifikasi menyebut topik yang dipahami (label aman), bukan echo mentah."""
    hints = {
        INTENT_CHECK_STOCK: "nama barangnya",
        INTENT_ASSET_DETAIL: "kode tag / nama / serial asetnya",
        INTENT_ASSET_USER: "kode tag / nama asetnya",
        INTENT_ASSET_HISTORY: "kode tag / nama asetnya",
        INTENT_ASSET_SEARCH: "kategorinya (mis. laptop, printer, radio) atau kondisinya",
        INTENT_UNIT_DETAIL: "kode unitnya (mis. DT-02) atau infonya — merek, status, aset terpasang",
        INTENT_ASSET_TOP: "kriterianya (mis. paling tua, paling baru, paling sering pindah/rusak)",
        INTENT_HANDOVER_LIST: "periode atau statusnya (mis. handover bulan ini, bast yang belum signed)",
        INTENT_REQUEST_STATUS: "jenis dan statusnya (mis. material request yang belum fulfilled)",
        INTENT_DAMAGE_LIST: "statusnya (mis. damage yang belum resolved)",
    }
    need = hints.get(intent, "detail pertanyaannya")
    tpl = _CLARIFICATION_TEMPLATES[
        _pick_variant(intent + "|" + need, len(_CLARIFICATION_TEMPLATES))]
    return tpl % need


# ============================================================================
# 9. Routing + capture reason (cermin confidenceRouting & CaptureReasonFor)
# ============================================================================

ROUTE_EXECUTE = "execute"
ROUTE_CLARIFY = "clarify"
ROUTE_HANDOVER = "handover"


def route(classification, configured=0.0):
    """floor = min(0.70, threshold): menurunkan ambang ikut menurunkan floor."""
    intent = classification["intent"]
    conf = classification["confidence"]
    floor = low_confidence_floor(intent, configured)
    threshold = confidence_threshold(intent, configured)
    if conf < floor:
        return ROUTE_HANDOVER
    if conf < threshold:
        return ROUTE_CLARIFY
    return ROUTE_EXECUTE


# Kata yang menandakan user bicara soal inventaris walau intent tak yakin.
# Dipakai backend: handover buta -> klarifikasi terarah bila sinyal ini ada.
_MEANINGFUL_SIGNALS = (
    set(CATEGORY_GAZETTEER) | set(_FLEET_ALIASES) | {
        "stok", "aset", "asset", "unit", "detail", "riwayat", "request",
        "handover", "bast", "fstb", "damage", "rusak", "maintenance",
        "servis", "terpasang", "pengguna", "pakai", "laporan", "pengajuan",
        "spesifikasi", "spek", "kategori", "tipe", "serial", "tag",
    }
)


def has_meaningful_signal(entities, raw_text=""):
    """True bila ada entitas terisi ATAU kata inventaris di teks.

    Backend memakainya agar kasus tak-yakin tidak langsung dilempar ke
    staff, melainkan ditanya balik secara spesifik dulu.
    """
    if entities:
        for key in ("asset_refs", "category", "radio_kind", "asset_type",
                    "form_kind", "form_status", "period", "state",
                    "condition", "employee_name"):
            if entities.get(key):
                return True
    toks = set(normalize_id(raw_text or "").split())
    return bool(toks & _MEANINGFUL_SIGNALS)


CAPTURE_UNKNOWN = "unknown"
CAPTURE_HANDOVER = "handover"
CAPTURE_OOD_SCOPE = "ood_scope_filter"
CAPTURE_CLARIFICATION = "clarification"
CAPTURE_DATA_MISS = "data_miss"
CAPTURE_LOW_CONFIDENCE = "low_confidence"


def capture_reason(output):
    """output: dict {intent, confidence, method, tool_executed, ood_reason}.

    Sinyal paling spesifik menang (cermin feedback.go WACS).
    """
    tool = output.get("tool_executed", "")
    if tool == "ood_scope_filter":
        return CAPTURE_OOD_SCOPE, True
    if tool == "clarification":
        return CAPTURE_CLARIFICATION, True
    if tool == "data_miss":
        return CAPTURE_DATA_MISS, True
    if output.get("handoff"):
        return CAPTURE_HANDOVER, True
    if output.get("intent") == INTENT_UNKNOWN:
        return CAPTURE_UNKNOWN, True
    intent = output.get("intent", "")
    conf = output.get("confidence", 0.0)
    if intent and conf > 0 and conf < confidence_threshold(intent):
        return CAPTURE_LOW_CONFIDENCE, True
    return "", False


def question_hash(text):
    """Kunci dedupe antrean (fnv-1a 64, abaikan kapital — cermin WACS)."""
    return "%016x" % _fnv1a64((text or "").strip().lower())


# ============================================================================
# 10. Self-test mandiri (cermin korpus golden: ukur, bukan melatih)
# ============================================================================

_SELF_TEST_CASES = [
    # (teks, intent harapan)
    ("halo", "greeting"), ("selamat pagi", "greeting"), ("assalamualaikum", "greeting"),
    ("makasih banyak", "thanks"), ("terima kasih infonya", "thanks"),
    ("dadah", "goodbye"), ("sampai jumpa", "goodbye"),
    ("kamu bisa apa", "help"), ("contoh pertanyaan", "help"), ("gimana cara pakai", "help"),
    ("kamu siapa", "identity"), ("fungsimu apa", "identity"), ("apa tugasmu", "identity"),
    ("siapa yang buat kamu", "creator"), ("siapa developernya", "creator"),
    ("siapa pembuat aplikasi ini", "creator"),
    ("rekap aset", "recap"), ("jumlah aset berapa", "recap"), ("ringkasan inventaris", "recap"),
    ("stok radio ht berapa", "check_stock"), ("berapa stok radio ht", "check_stock"), ("sisa kabel lan", "check_stock"),
    ("stok menipis apa saja", "check_stock"), ("stock mouse habis kah", "check_stock"),
    ("brp sisa tinta", "check_stock"), ("stoknya masih ada gak", "check_stock"),
    ("laptop tersedia apa saja", "asset_search"), ("aset rusak ada berapa", "asset_search"),
    ("radio yang dipakai", "asset_search"), ("printer broken", "asset_search"),
    ("detail ITLT-007", "asset_detail"), ("kondisi PRN-01", "asset_detail"),
    ("spesifikasi aset itu", "asset_detail"), ("ITLT-007", "asset_detail"),
    ("siapa yang pakai ITLT-007", "asset_user"), ("pengguna printer itu siapa", "asset_user"),
    ("asetnya budi apa saja", "asset_user"), ("laptop ini dipegang siapa", "asset_user"),
    ("riwayat ITLT-007", "asset_history"), ("histori printer itu", "asset_history"),
    ("pernah dipegang siapa saja", "asset_history"),
    ("riwayat servis printer", "maintenance_list"), ("biaya perbaikan laptop", "maintenance_list"),
    ("daftar handover terakhir", "handover_list"), ("fstb bulan lalu", "handover_list"),
    ("damage report belum resolved", "damage_list"), ("laporan kerusakan minggu ini", "damage_list"),
    ("status request saya", "request_status"), ("material request sudah approve belum", "request_status"),
    ("hubungi teknisi", "human_agent"), ("minta nomor staff it", "human_agent"),
    # V2 fuzzy ref (goals2.md §9) — ukur, bukan latih (jangan salin ke EXEMPLARS)
    ("carikan itlt-002", "asset_detail"), ("carikan itlt02", "asset_detail"),
    ("carikan ITLT 002", "asset_detail"), ("itlt02", "asset_detail"),
    # Unit/fleet (goals2.md §13): kode DT/EX/LV/WT selalu soal unit
    ("dt 02", "unit_detail"), ("dt.02", "unit_detail"),
    ("dt02", "unit_detail"), ("DT-02", "unit_detail"), ("LV 2", "unit_detail"),
    ("dt 02.07 itu merek apa", "unit_detail"), ("merek dt-02 apa", "unit_detail"),
    ("status unit ex-05", "unit_detail"),
    ("riwayat dt 02", "asset_history"), ("siapa pakai itlt02", "asset_user"),
    ("exca", "asset_search"), ("cari exca yang breakdown", "asset_search"),
    # Domain: HT vs Rig, IT vs Operasional, tipe/kategori/spek (goals2.md §11)
    ("radio ht yang tersedia", "asset_search"), ("radio rig yang rusak", "asset_search"),
    ("ht", "asset_search"), ("rig", "asset_search"),
    ("aset it apa saja", "asset_search"), ("aset operasional yang dipakai", "asset_search"),
    ("tampilkan aset operasional", "asset_search"),
    ("tipe aset ini", "asset_search"), ("kategori laptop", "asset_search"),
    ("desktop tersedia", "asset_search"), ("spek ITLT-007", "asset_detail"),
    # Form: handover/request/damage + status/periode (goals2.md §13)
    ("handover bulan ini", "handover_list"), ("bast kemarin", "handover_list"),
    ("serah terima tanggal berapa", "handover_list"),
    ("material request yang sudah fulfilled", "request_status"),
    ("pengajuan material yang belum", "request_status"),
    ("asset request approved", "request_status"),
    ("permintaan akun saya", "request_status"),
    ("damage yang sudah resolved", "damage_list"),
    ("barang hilang", "damage_list"),
    # Ranking (goals2.md §13)
    ("aset yg paling tua", "asset_top"),
    ("aset yang paling banyak pindah", "asset_top"),
    ("riwayat aset it yg paling banyak pindah", "asset_top"),
    ("laptop paling baru", "asset_top"),
    ("aset paling sering rusak", "asset_top"),
    # Consumable lapangan: kata barang -> cek stok (goals2.md §13)
    ("konektor?", "check_stock"), ("daptor bnc", "check_stock"),
    ("sisa stok radio rig", "check_stock"), ("antena masih ada?", "check_stock"),
    # Kategori polos + prefix tag (data asli: ITCT-032 = CCTV)
    ("cctv", "asset_search"), ("list cctv", "asset_search"),
    ("cctv atau itct", "asset_search"), ("itct", "asset_search"),
    ("desktop", "asset_search"),
    # OOD → unknown
    ("cuaca hari ini bagaimana", "unknown"), ("12 + 34 berapa", "unknown"),
    ("jam berapa sekarang", "unknown"), ("kamu manusia atau robot", "unknown"),
    ("resep rendang", "unknown"), ("siapa presiden pertama", "unknown"),
    # F3/F6 regresi insiden lapangan: langit OOD, biru+IT tetap inventaris,
    # "bulan" periode tak boleh jadi OOD
    ("kenapa langit warna biru?", "unknown"),
    ("bintang di langit apa saja?", "unknown"),
    ("cctv biru", "asset_search"),
    ("printer biru yang tersedia", "asset_search"),
    ("handover bulan ini", "handover_list"),
]


def _self_test_handover_bulan_ini():
    """Jaga: 'bulan' tak pernah jadi pemicu OOD (periode form)."""
    r, ok = ood_match("handover bulan ini")
    assert not ok, ("handover bulan ini dianggap OOD: %s" % r)
    r, ok = ood_match("serah terima bulan lalu")
    assert not ok, ("serah terima bulan lalu dianggap OOD: %s" % r)


_SELF_TEST_OOD_REASONS = {
    "cuaca hari ini bagaimana": OOD_WEATHER,
    "kenapa langit warna biru?": OOD_WEATHER,
    "jam berapa sekarang": OOD_CLOCK,
    "kamu manusia atau robot": OOD_IDENTITY,
    "resep rendang": OOD_TRIVIA,
}


def run_self_test():
    """Ukur akurasi klasifikasi sampel (target merujuk WACS: in-domain ≥85%)."""
    good = 0
    total = 0
    failures = []
    for text, expected in _SELF_TEST_CASES:
        total += 1
        got = classify(text)["intent"]
        if got == expected:
            good += 1
        else:
            failures.append((text, expected, got))
    ood_ok = 0
    for text, reason in _SELF_TEST_OOD_REASONS.items():
        r, ok = ood_match(text)
        if ok and r == reason:
            ood_ok += 1
        else:
            failures.append((text, "ood:" + reason, "ood:%s ok=%s" % (r, ok)))
    acc = good / total * 100 if total else 0
    print("NLU self-test: %d/%d benar (%.1f%%), OOD reason %d/%d" %
          (good, total, acc, ood_ok, len(_SELF_TEST_OOD_REASONS)))
    for text, expected, got in failures:
        print("  FAIL %-42r harap=%s dapat=%s" % (text, expected, got))
    # ATURAN TETAP: sampel uji tidak boleh disalin ke EXEMPLARS.
    _self_test_handover_bulan_ini()
    print("OOD bulan-guard: OK (periode form tak pernah OOD)")
    _self_test_llm_path()
    return acc


def _self_test_llm_path():
    """Uji jalur L1 tanpa server: prompt, validator, injeksi transport."""
    prompt = decision_system_prompt()
    missing = [i for i in ALL_INTENTS if i not in prompt]
    print("LLM prompt: %s (%d intent tercakup)" %
          ("OK" if not missing else "KURANG %s" % missing, len(ALL_INTENTS)))

    valid = parse_decision(
        '{"intent": "check_stock", "entities": {"item": "kabel"}, '
        '"constraints": {"low_only": true}, "confidence": 0.9}')
    assert valid and valid["intent"] == "check_stock" \
        and valid["constraints"] == {"low_only": True}, valid
    for bad in ["", "bukan json", '{"intent": "order_pizza", "confidence": 0.9}',
                '{"intent": "unknown", "confidence": 0.9}',
                '{"intent": "check_stock"}']:
        if bad == '{"intent": "check_stock"}':
            got = parse_decision(bad)  # confidence default 0.0, tetap valid
            assert got and got["confidence"] == 0.0, got
        else:
            assert parse_decision(bad) is None, bad

    global LLM_ENABLED
    old = LLM_ENABLED
    LLM_ENABLED = True
    try:
        assert llm_decide("stok kabel") is None  # tanpa transport → menyerah
        fake = lambda _s, _u: '{"intent": "recap", "confidence": 0.88}'
        got = llm_decide("rekap dong", transport=fake)
        assert got and got["intent"] == "recap", got
        assert llm_decide("x", transport=lambda _s, _u: (_ for _ in ()).throw(
            TimeoutError())) is None  # transport gagal → menyerah
    finally:
        LLM_ENABLED = old
    print("LLM path: OK (validator + fallback tanpa server)")


if __name__ == "__main__":
    run_self_test()
