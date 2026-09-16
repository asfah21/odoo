Perbaiki WACS agar chatbot mampu memahami percakapan secara kontekstual dan luwes, bukan mengklasifikasikan setiap pesan user secara terisolasi.

## Tujuan utama

Masalah saat ini:

```text
User: stok kabel
AI: Biar tepat sasaran, sebutkan nama barangnya.

User: berapa stok kabel
AI: Biar tepat sasaran, sebutkan nama barangnya.

User: stok kabel antena
AI: Biar tepat sasaran, sebutkan nama barangnya.
```

Padahal intent user jelas: mencari stok barang, lalu memperjelas barang menjadi kabel antena.

Perbaiki pipeline agar pesan baru selalu dipahami bersama conversation state/history yang relevan.

---

## Prinsip utama

### 1. Conversation ≠ Intent

Jangan menganggap setiap pesan baru sebagai percakapan baru.

Contoh:

```text
User: stok kabel
```

harus dapat dipahami sebagai:

```json
{
  "intent": "STOCK_QUERY",
  "product": "kabel"
}
```

Kemudian:

```text
User: kabel antena
```

harus dipahami sebagai refinement:

```json
{
  "intent": "STOCK_QUERY",
  "product": "kabel antena",
  "relation": "REFINE"
}
```

Bukan sebagai intent UNKNOWN.

---

## 2. Tambahkan/gunakan Conversation State

Audit kode yang sudah ada terlebih dahulu.

Cari mekanisme yang sekarang menyimpan:

* conversation
* messages
* previous intent
* entities
* context
* memory
* classifier result
* business state

Jangan membuat sistem memory kedua jika mekanisme yang diperlukan sudah tersedia.

Gunakan state yang sudah ada dan lakukan perubahan sekecil mungkin.

Conversation state minimal harus mampu mempertahankan:

```text
current_domain
current_conversation_type
current_intent
current_entities
previous_intent
previous_entities
last_user_message
last_assistant_message
```

Tidak perlu menyimpan seluruh history ke prompt Qwen jika tidak diperlukan.

---

## 3. Tambahkan Conversation Relation

Setiap pesan baru perlu dapat dikategorikan hubungannya dengan konteks sebelumnya.

Gunakan minimal:

```text
NEW
FOLLOW_UP
REFINE
CORRECT
REPLACE
CONTINUE
CONFIRM
DENY
CANCEL
SAME_TOPIC
```

Contoh:

```text
User: stok radio HT
AI: Ada 12 unit.

User: yang rusak?
```

hasil:

```json
{
  "intent": "STOCK_QUERY",
  "product": "radio HT",
  "condition": "DAMAGED",
  "relation": "FOLLOW_UP"
}
```

Contoh refinement:

```text
User: stok kabel
User: kabel antena
```

hasil:

```json
{
  "intent": "STOCK_QUERY",
  "product": "kabel antena",
  "relation": "REFINE"
}
```

Contoh correction:

```text
User: stok radio HT
User: eh maksud saya radio base
```

hasil:

```json
{
  "intent": "STOCK_QUERY",
  "product": "radio base",
  "relation": "CORRECT"
}
```

---

## 4. UNKNOWN jangan langsung menjadi clarification

Saat classifier menghasilkan:

```text
UNKNOWN
```

JANGAN langsung mengembalikan:

> "Bisa diperjelas nama barangnya?"

Lakukan resolusi berurutan:

```text
Current message
      ↓
Classify current message
      ↓
Check conversation state
      ↓
Is this FOLLOW_UP?
      ↓
Is this REFINE?
      ↓
Is this CORRECT?
      ↓
Can previous intent/entity resolve it?
      ↓
Entity/alias/fuzzy resolution
      ↓
Business logic
      ↓
Clarification only if genuinely necessary
```

Klarifikasi hanya dilakukan jika konteks memang tidak cukup untuk menentukan maksud.

---

## 5. Entity harus bisa diwariskan dari conversation

Contoh:

```text
User: stok radio HT
AI: Ada 12 unit.

User: yang rusak?
```

Jangan menghasilkan:

```text
intent = UNKNOWN
```

Wariskan:

```text
intent = STOCK_QUERY
product = radio HT
condition = damaged
```

Contoh:

```text
User: stok kabel antena
AI: Ada 8 buah.

User: yang gudang Wolo?
```

hasil:

```text
intent = STOCK_QUERY
product = kabel antena
location = Wolo
```

Entity baru harus dapat ditambahkan ke context lama.

---

## 6. Entity Resolution

Gunakan mekanisme dictionary/alias/fuzzy yang sudah ada.

Contoh:

```text
hp
hape
handphone
ponsel
phone
```

dapat diarahkan ke canonical entity yang sama.

Untuk inventory:

```text
kabel
kabel antena
kabel coax
coax
RG58
```

harus dicocokkan dengan product/category database.

Penting:

```text
"kabel"
```

tidak boleh dipaksa menjadi product tertentu jika database memiliki banyak jenis kabel.

Jika ambiguous, barulah clarification:

> "Ada beberapa jenis kabel. Maksudnya kabel antena, LAN, power, atau yang lain?"

Tetapi:

```text
"kabel antena"
```

harus dicoba di-resolve ke product database sebelum meminta klarifikasi.

---

## 7. Pisahkan Understanding dari Business Logic

Pipeline ideal:

```text
User Message
    ↓
Conversation Understanding
    ↓
Context Resolution
    ↓
Entity Resolution
    ↓
Business Intent
    ↓
Database Query
    ↓
Result
    ↓
Qwen Response Generation
```

Qwen tidak boleh dijadikan sumber kebenaran inventory.

Qwen hanya membantu:

* memahami bahasa natural jika diperlukan
* memahami follow-up jika deterministic resolver tidak cukup
* merangkai jawaban
* membuat respons lebih natural

Data stok tetap berasal dari database/business logic.

Jangan membuat Qwen mengarang jumlah stok, nama barang, kode aset, harga, atau data inventory.

---

## 8. Pertahankan desain domain dan conversation type

Jangan menghapus atau menyederhanakan konsep yang sudah ada.

Tetap bedakan:

```text
domain
conversation_type
intent
entity
sentiment/emotion
```

Contoh:

```text
domain = OUT_OF_DOMAIN
conversation_type = COMPLIMENT
```

tetap valid.

Contoh:

```text
domain = IT
conversation_type = QUERY
intent = STOCK_QUERY
```

juga valid.

OUT_OF_DOMAIN tidak otomatis berarti refusal.

Conversation type seperti:

```text
GREETING
THANKS
COMPLIMENT
FLIRT
ROMANTIC
POETIC
JOKE
INSULT
```

tetap harus diproses secara natural sesuai logic yang sudah ada.

Jangan merusak behavior tersebut saat memperbaiki stock conversation.

---

## 9. Multi-turn examples yang WAJIB berhasil

Setelah implementasi, buat test untuk kasus berikut.

### Test A

```text
User: stok kabel
User: berapa?
```

Expected:

```text
intent tetap STOCK_QUERY
product tetap kabel
```

### Test B

```text
User: stok kabel
User: kabel antena
```

Expected:

```text
intent = STOCK_QUERY
product = kabel antena
relation = REFINE
```

### Test C

```text
User: stok radio HT
User: yang rusak?
```

Expected:

```text
intent = STOCK_QUERY
product = radio HT
condition = DAMAGED
relation = FOLLOW_UP
```

### Test D

```text
User: stok radio HT
User: eh maksud saya radio base
```

Expected:

```text
intent = STOCK_QUERY
product = radio base
relation = CORRECT
```

### Test E

```text
User: stok kabel antena
User: di gudang mana?
```

Expected:

```text
intent = STOCK_QUERY
product = kabel antena
relation = FOLLOW_UP
```

dan menjawab berdasarkan data lokasi inventory.

### Test F

```text
User: stok kabel
AI: Ada beberapa jenis kabel...
User: antena
```

Expected:

```text
intent = STOCK_QUERY
product = kabel antena
```

bukan:

```text
UNKNOWN
```

### Test G

```text
User: stok kabel antena
User: yang menipis?
```

Expected:

```text
intent = LOW_STOCK_QUERY
product = kabel antena
relation = FOLLOW_UP
```

### Test H

```text
User: stok kabel antena
User: berapa?
User: yang Wolo?
User: yang rusak?
```

Semua harus tetap membawa context yang relevan:

```text
product = kabel antena
location = Wolo
condition = damaged
```

tanpa user harus mengulang:

```text
kabel antena
```

setiap kali.

---

## 10. Jangan over-engineer

Sebelum coding:

1. Audit architecture WACS.
2. Temukan classifier/intent engine yang sekarang.
3. Temukan conversation/history/state yang sudah ada.
4. Temukan entity/dictionary/fuzzy resolver.
5. Temukan business query/database layer.
6. Temukan Qwen agent.

Kemudian tentukan titik perubahan paling kecil.

Jangan:

* rewrite seluruh architecture
* mengganti database
* mengganti model Qwen
* membuat framework baru
* membuat memory system baru
* membuat classifier baru jika classifier existing masih dapat digunakan
* menghapus intent yang sudah ada
* mengubah API contract tanpa alasan kuat

Prioritaskan reuse komponen existing.

---

## 11. Logging/debugging

Tambahkan logging/debug information yang mudah dilihat saat development:

```text
MESSAGE
INTENT
RELATION
PREVIOUS_INTENT
ENTITIES
RESOLVED_ENTITIES
BUSINESS_ACTION
```

Contoh:

```text
MESSAGE: "yang rusak?"

INTENT: STOCK_QUERY
RELATION: FOLLOW_UP

PREVIOUS:
  intent: STOCK_QUERY
  product: radio HT

RESOLVED:
  product: radio HT
  condition: damaged

ACTION:
  query_stock
```

Logging jangan membocorkan data sensitif.

---

## 12. Behavior yang diinginkan

Target akhirnya adalah chatbot yang terasa seperti manusia mengikuti percakapan.

Contoh:

```text
User: stok kabel
AI: Ada beberapa jenis kabel. Mau kabel antena, LAN, atau power?

User: antena
AI: Kabel antena saat ini ada 8 pcs.

User: yang Wolo?
AI: Di Wolo ada 5 pcs.

User: yang rusak?
AI: Ada 1 pcs yang tercatat rusak.
```

User tidak perlu mengulang konteks pada setiap pesan.

---

## Acceptance Criteria

Perbaikan dianggap berhasil jika:

1. Follow-up dapat menggunakan intent sebelumnya.
2. Entity dapat diwariskan dari context.
3. Entity baru dapat mempersempit entity sebelumnya.
4. Correction dapat mengganti entity sebelumnya.
5. UNKNOWN tidak otomatis menghasilkan clarification.
6. Clarification hanya muncul jika konteks memang ambigu/tidak cukup.
7. Inventory data tetap berasal dari database.
8. Qwen tidak boleh mengarang data inventory.
9. Domain/conversation_type yang sudah ada tetap bekerja.
10. Existing behavior tidak rusak.
11. Multi-turn conversation minimal 4–5 turn dapat diproses dengan benar.
12. Perubahan kode seminimal mungkin dan mengikuti architecture WACS yang sekarang.

Setelah selesai, tampilkan:

```text
1. Root cause
2. File yang diubah
3. Perubahan setiap file
4. Flow sebelum vs sesudah
5. Test yang dibuat/dijalankan
6. Hasil test
7. Risiko/regression yang masih tersisa
```

Jangan langsung melakukan rewrite besar. Audit terlebih dahulu, lalu implementasikan solusi paling kecil yang memenuhi acceptance criteria.
