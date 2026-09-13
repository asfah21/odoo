# -*- coding: utf-8 -*-
"""Ask AI Personas — Alya & Raka (stdlib pure, tanpa ``import odoo``).

Dua persona yang bisa dipilih di menu Ask AI -> Setting:

- **Alya** — ramah, sopan, perhatian. Gaya: short, casual, emoji ringan.
- **Raka** — laki-laki, remaja, friendly, helpful. Gaya: short, casual,
  natural, youthful, conversational, emoji ringan. Nama bisa dikonfigurasi.

Keduanya berbagi kontrak perilaku:

- Bahasa: Indonesia, atau English bila user memakai English.
- Tidak memaksa, menawarkan bantuan, mengakui jika tidak tahu,
  meminta klarifikasi bila info kurang, memahami konteks.
- Restrictions (dipetakan ke guardrail yang SUDAH ada di backend):
  no hallucination / no invented stock-product-price -> jawaban faktual
  HANYA dari evidence ORM (data_miss jujur bila kosong); no fake
  discount (bot tak pernah menampilkan diskon); no false promises
  (tak-yakin -> klarifikasi/handover, bukan janji); FACT dari WACS
  = source of truth = evidence ORM.

File ini murni stdlib sehingga bisa diuji mandiri::

    python ask_ai_persona.py
"""

import re as _re

PERSONA_ALYA = "alya"
PERSONA_RAKA = "raka"
ALL_PERSONAS = (PERSONA_ALYA, PERSONA_RAKA)

PERSONAS = {
    PERSONA_ALYA: {
        "default_name": "Alya",
        "identity": ["Alya"],
        "tone": ["ramah", "sopan", "perhatian"],
        "tone_en": ["friendly", "polite", "caring"],
        "languages": ["Indonesia", "English bila user memakai English"],
        "behavior": ["tidak memaksa", "menawarkan bantuan",
                     "mengakui jika tidak tahu", "tidak mengarang"],
        "style": ["short response", "casual", "emoji ringan"],
        "restrictions": ["no hallucination", "no fake discount",
                         "no invented stock"],
    },
    PERSONA_RAKA: {
        "default_name": "Raka",
        "identity": ["configurable name"],
        "character": ["laki-laki", "remaja", "friendly", "helpful"],
        "character_en": ["male", "teen", "friendly", "helpful"],
        "tone": ["ramah", "sopan", "santai", "perhatian"],
        "tone_en": ["friendly", "polite", "relaxed", "caring"],
        "languages": ["Bahasa Indonesia", "English bila user memakai English"],
        "behavior": ["tidak memaksa", "responsif",
                     "menawarkan bantuan bila relevan",
                     "memahami konteks percakapan",
                     "mengakui jika tidak tahu",
                     "meminta klarifikasi jika informasi kurang",
                     "tidak mengarang informasi"],
        "style": ["short response", "casual", "natural", "youthful",
                  "conversational", "emoji ringan"],
        "restrictions": ["no hallucination", "no fake discount",
                         "no invented stock", "no invented product",
                         "no invented price", "no false promises",
                         "FACT dari WACS adalah source of truth"],
    },
}


def normalize_persona(key):
    """Kembalikan key valid ('alya'/'raka'); tak dikenal -> 'alya'."""
    return key if key in ALL_PERSONAS else PERSONA_ALYA


def display_name(persona_key, configured=""):
    """Nama tampil persona: konfigurasi user menang, fallback default."""
    name = (configured or "").strip()
    if name:
        return name[:40]
    return PERSONAS[normalize_persona(persona_key)]["default_name"]


def summary(persona_key):
    """Ringkasan traits untuk layar Setting (English)."""
    key = normalize_persona(persona_key)
    p = PERSONAS[key]
    lines = ["Identity: %s" % ", ".join(p.get("identity", []))]
    if p.get("character_en"):
        lines.append("Character: %s" % ", ".join(p["character_en"]))
    lines.append("Tone: %s" % ", ".join(p.get("tone_en", [])))
    lines.append("Behavior: %s" % "; ".join(
        _behavior_en(b) for b in p.get("behavior", [])))
    lines.append("Style: %s" % ", ".join(p.get("style", [])))
    lines.append("Restrictions: %s" % "; ".join(p.get("restrictions", [])))
    return "\n".join(lines)


_BEHAVIOR_EN = {
    "tidak memaksa": "non-pushy",
    "menawarkan bantuan": "offers help",
    "menawarkan bantuan bila relevan": "offers help when relevant",
    "mengakui jika tidak tahu": "admits when it doesn't know",
    "tidak mengarang": "never fabricates",
    "tidak mengarang informasi": "never fabricates information",
    "responsif": "responsive",
    "memahami konteks percakapan": "understands conversation context",
    "meminta klarifikasi jika informasi kurang":
        "asks for clarification when info is lacking",
}


def _behavior_en(text):
    return _BEHAVIOR_EN.get(text, text)


# ============================================================================
# Language detection (ringan, heuristik kata — cukup untuk pilih varian EN)
# ============================================================================

_EN_MARKERS = frozenset(
    "the is are was were what what`s what's how why when which who whom "
    "please thanks thank you your yours my our hello hi hey bye goodbye "
    "can could would should do does did not no yes ok okay use user using "
    "used check show me give tell know kind more most many much very just "
    "like want need help stock available bruh bro".split())
_ID_MARKERS = frozenset(
    "yang dan atau dengan untuk dari saya kamu anda kak min mas mbak pak bu "
    "berapa stok sisa tolong dong kah nya apa siapa dimana mana gw gue lu lo "
    "kasih liat tengok coba deh sih aja saja tidak nggak gak sudah belum bisa "
    "mau ada adalah ini itu tersebut terima kasih halo hai selamat pagi siang "
    "sore malam".split())


def detect_language(text):
    """'en' bila sinyal Inggris menang, selain itu 'id'."""
    toks = _re.findall(r"[a-z']+", (text or "").lower())
    if not toks:
        return "id"
    en_hit = sum(1 for t in toks if t in _EN_MARKERS)
    id_hit = sum(1 for t in toks if t in _ID_MARKERS)
    return "en" if en_hit > id_hit and en_hit > 0 else "id"


# ============================================================================
# Persona name stripping — "Alya, stok radio?" -> "stok radio?"
# ============================================================================

def strip_persona_name(text, names):
    """Kupas panggilan nama di awal/akhir ('Alya, ...', '... ya Raka').

    ``names``: iterable nama (case-insensitive). Kembalikan teks sisa
    (bisa '' bila user HANYA memanggil nama -> backend membaca greeting).
    """
    t = (text or "").strip()
    touched = False
    for name in names or []:
        n = (name or "").strip()
        if not n or len(n) < 2:
            continue
        if t.lower() == n.lower():
            return ""  # hanya memanggil nama -> backend membaca greeting
        pat = _re.compile(r"^[\s,.:;!\-]*%s[\s,.:;!\-]+(.*)$"
                          % _re.escape(n), _re.I | _re.S)
        m = pat.match(t)
        if m:
            t = m.group(1).strip()
            touched = True
            continue
        pat2 = _re.compile(r"^(.*)[\s,.:;!\-]+%s[\s,.:;!\-]*$"
                           % _re.escape(n), _re.I | _re.S)
        m2 = pat2.match(t)
        if m2 and m2.group(1).strip():
            t = m2.group(1).strip()
            touched = True
    if touched:
        # kupas partikel sisa ("stok radio ya" -> "stok radio")
        t = _re.sub(r"\s+(ya|dong|kah|deh|sih|kok|nih|lho|loh)[\s,.:;!\-]*$",
                    "", t, flags=_re.I).strip()
    return t


# ============================================================================
# Social EN variants + identity text (dipakai backend bila lang == 'en',
# dan identity selalu memakai nama persona aktif)
# ============================================================================

SOCIAL_EN = {
    "greeting": ("Hello! 👋 I'm <b>{name}</b> — I read the IT module's "
                 "<b>live data</b>: stock, assets, users, and history.<br/>Try: "
                 "<i>“radio ht stock?”</i> • <i>“asset recap”</i>"),
    "thanks": "You're welcome! 🙏 Anything else to check in the IT data?",
    "goodbye": "Bye! 👋 Find me at <b>IT → Ask AI</b> anytime.",
    "help": ("I can answer from <b>live data</b>: <b>stock</b>, "
             "<b>asset recap</b>, <b>users</b>, <b>history</b>, "
             "<b>units/fleet</b>, <b>handover</b>, and <b>requests</b>.<br/>"
             "Example: <i>“available radio ht?”</i>"),
    "creator": ("I was built by <b>Azvan</b> — <b>IT Department, PT GSI "
                "(Site Wolo)</b> 🛠️<br/>Feedback? Tell the Site Wolo IT team."),
}


def identity_text(persona_key, name, lang="id"):
    """Jawaban 'kamu siapa' memakai nama persona aktif."""
    if lang == "en":
        return ("I'm <b>%s</b> 🤖 — the GSI IT inventory assistant "
                "(<b>PT GSI, Site Wolo</b>).<br/>I answer from the "
                "<b>live IT module data</b>: stock, assets, users, condition, "
                "history, maintenance, handover, damage, and requests."
                % _esc(name))
    return ("Saya <b>%s</b> 🤖 — asisten inventaris IT "
            "<b>PT GSI, Site Wolo</b>.<br/>Fungsi saya: menjawab dari <b>data live "
            "modul IT</b> — stok, aset, pengguna, kondisi, riwayat, maintenance, "
            "handover, damage, dan request." % _esc(name))


def _esc(value):
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def rephrase_persona_block(persona_key, name, lang="id"):
    """Blok persona untuk system prompt rephrase Qwen (pendek, hemat token)."""
    key = normalize_persona(persona_key)
    p = PERSONAS[key]
    tone = ", ".join(p.get("tone_en", []))
    style = ", ".join(p.get("style", []))
    rules = "; ".join(p.get("restrictions", []))
    lang_rule = ("Reply in English (the user wrote in English)."
                 if lang == "en"
                 else "Reply in Bahasa Indonesia.")
    return ("Persona: %s — %s. Style: %s. %s Hard rules: %s"
            % (name or p["default_name"], tone, style, lang_rule, rules))


# ============================================================================
# Self-test mandiri
# ============================================================================

_SELF_TEST_LANG = [
    ("stok radio ht berapa?", "id"),
    ("siapa yang pakai ITLT-007?", "id"),
    ("terima kasih banyak", "id"),
    ("what is the radio ht stock?", "en"),
    ("who uses ITLT-007?", "en"),
    ("please show me broken laptops", "en"),
    ("hello, thanks", "en"),
    ("ITLT-007", "id"),
]

_SELF_TEST_STRIP = [
    (("Alya", "Raka"), "Alya, stok radio ht?", "stok radio ht?"),
    (("Alya", "Raka"), "stok radio ht ya Raka", "stok radio ht"),
    (("Alya",), "HALO ALYA", "HALO"),
    (("Alya",), "Alya", ""),
    (("Raka",), "stok radio ht?", "stok radio ht?"),
    (("  Alya  ",), "  Alya, rekap aset", "rekap aset"),
]


def run_self_test():
    good, total, fails = 0, 0, []
    for text, want in _SELF_TEST_LANG:
        total += 1
        got = detect_language(text)
        if got == want:
            good += 1
        else:
            fails.append(("lang", text, want, got))
    for names, text, want in _SELF_TEST_STRIP:
        total += 1
        got = strip_persona_name(text, names)
        if got == want:
            good += 1
        else:
            fails.append(("strip", text, want, got))
    total += 1
    if normalize_persona("x") == "alya" and display_name("raka", "") == "Raka" \
            and display_name("alya", " Alya-Wolo ") == "Alya-Wolo":
        good += 1
    else:
        fails.append(("persona", "defaults", "alya/Raka", "?"))
    total += 1
    blk = rephrase_persona_block("raka", "Raka", "en")
    if "Raka" in blk and "English" in blk and "no invented price" in blk:
        good += 1
    else:
        fails.append(("rephrase", "raka-en", "has name/lang/rules", blk))
    total += 1
    if "Alya" in identity_text("alya", "Alya", "id") \
            and "live IT module data" in identity_text("alya", "Alya", "en"):
        good += 1
    else:
        fails.append(("identity", "name/EN", "ok", "?"))
    print("persona self-test: %d/%d benar (%.1f%%)"
          % (good, total, good / total * 100 if total else 0))
    for kind, case, want, got in fails:
        print("  FAIL [%s] %r harap=%r dapat=%r" % (kind, case, want, got))
    return good == total


if __name__ == "__main__":
    run_self_test()
