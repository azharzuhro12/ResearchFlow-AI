# ResearchFlow AI

**Platform Otomasi Riset AI Otonom — dari satu pertanyaan menjadi laporan riset ber-sitasi tervalidasi.**

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-siap-2496ED?logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/status-selesai-success)

Riset apa pun — sekali jalan atau terjadwal, dengan **hasil riset dikirim
langsung ke Discord** (ringkasan, temuan utama, statistik, dan sitasi
tervalidasi) setiap laporan selesai.

---

## Daftar Isi

- [Tentang Proyek](#tentang-proyek)
- [Fitur Utama](#fitur-utama)
- [Cara Pakai](#cara-pakai)
- [Arsitektur](#arsitektur)
- [Cara Kerja Pipeline](#cara-kerja-pipeline)
- [Penjadwal](#penjadwal)
- [Notifikasi Discord](#notifikasi-discord)
- [Persistensi Data](#persistensi-data)
- [Referensi API](#referensi-api)
- [Docker](#docker)
- [Struktur Proyek](#struktur-proyek)
- [Pengujian & Pengembangan Lokal](#pengujian--pengembangan-lokal)
- [Keamanan](#keamanan)
- [Keterbatasan](#keterbatasan)
- [Rencana Pengembangan](#rencana-pengembangan)

---

## Tentang Proyek

ResearchFlow AI adalah asisten riset lokal end-to-end. Kamu memberi satu
pertanyaan; ia menyusun rencana riset, mencari di web gratis, mengekstrak
dan mengindeks sumber ke basis data vektor lokal, mensintesis jawaban
ber-grounding dengan sitasi inline yang **divalidasi di sisi server**,
merender laporan Markdown + PDF, dan bisa melakukan semuanya secara
otomatis sesuai jadwal — lalu mengirim hasilnya ke Discord.

Semua berjalan lokal kecuali dua hal: **GLM API** (satu-satunya
dependensi berbayar) dan pencarian web gratis berbasis DuckDuckGo.
Tidak ada API key lain, tidak ada vector database di cloud, tidak ada
broker, tidak ada SaaS.

### Masalah yang Diselesaikan

Riset manual berarti mengelola belasan tab browser: mencari, menyisir
halaman, menilai relevansi, mencatat asal tiap klaim, lalu menulis.
Hasilnya lambat, dan masalah *"ini aku baca di mana ya?"* membuat hasil
akhir sulit dipercaya atau direproduksi. Chatbot generik justru
memperburuk kepercayaan: menjawab lancar tapi jarang menunjukkan sumber
mana yang mendukung klaim mana, dan bisa mengarang dukungan secara diam-diam.

Untuk kebutuhan riset berulang ("ringkas perkembangan X tiap Senin
pagi"), tidak ada solusi mainstream yang mengotomasi *seluruh* putaran —
cari → baca → sitasi → tulis → kirim — tanpa tumpukan SaaS berbayar.

### Pendekatan Solusi

ResearchFlow AI mengotomasi putaran itu, dengan integritas sitasi sebagai
kriteria wajib, bukan bonus:

- **Satu pertanyaan masuk, satu laporan keluar** — pipeline penuh berjalan
  tuntas: rencana → query → pencarian → ekstraksi → chunking → embedding →
  indeks → retrieval → sintesis → validasi sitasi → render Markdown + PDF.
- **Sitasi yang bisa dibuktikan backend.** Bukti dinomori `[E1]…[En]`
  sebelum dilihat model; setelah generasi, tiap sitasi divalidasi ulang di
  sisi server dan dipetakan ke metadata sumber asli. **URL dan metadata
  sitasi buatan LLM tidak pernah dipercaya** selama metadata backend ada.
  Jawaban dengan sitasi tak berpijak di-retry, lalu ditolak sebagai
  `ungrounded` — bukan dikirim.
- **Otonom dan berulang.** Pertanyaan apa pun bisa menjadi jadwal interval
  atau cron di zona waktu IANA mana pun. Satu eksekusi = tepat satu putaran
  pencarian, satu pengindeksan, satu sintesis, satu pasangan laporan —
  tidak ada tahap ganda, tidak ada loop liar.
- **Semua tersimpan lokal.** Jadwal, riwayat eksekusi, dan metadata laporan
  hidup di SQLite; vector store dan laporan tersimpan di disk. Restart
  backend — atau seluruh stack Docker — tidak kehilangan apa pun.

---

## Fitur Utama

- 📋 **Rencana riset & query pencarian ber-AI** (GLM) dengan fallback
  deterministik — pencarian selalu jalan meski GLM tidak tersedia
- 🔎 **Pencarian web otomatis gratis** via DuckDuckGo (`ddgs`, tanpa API
  key) — deduplikasi lintas query + pembatasan jumlah hasil
- 🛡️ **Ekstraksi konten sumber yang aman** — fetch berbatas dengan
  penjaga SSRF + pembersihan teks
- ✂️ **Chunking deterministik** (1.000 karakter, overlap 150, aman batas kata)
- 🧲 **Embedding lokal** Sentence Transformers (`all-MiniLM-L6-v2`)
- 💾 **ChromaDB vektor store lokal persisten** dengan upsert idempoten
  (indeks ulang = mengganti, bukan menduplikasi)
- 🔬 **Sintesis GLM ber-grounding** dengan sitasi inline `[E1]…[En]`
- ✅ **Validasi sitasi di sisi server** dengan pemetaan sumber deterministik
- 🪡 **Pertahanan prompt-injection** — konten web yang diambil diperlakukan
  strictly sebagai data, tidak pernah sebagai instruksi
- 📄 **Laporan Markdown + PDF** — digenerate penuh secara offline (ReportLab)
- ⏰ **Riset terjadwal berulang** — interval atau cron lima field, zona
  waktu IANA, pause/resume/jalankan manual, penjaga konkurensi, batas frekuensi
- 📣 **Notifikasi Discord per eksekusi** — satu embed berisi **hasil riset**:
  ringkasan, temuan utama, statistik sumber/bukti, dan sitasi tervalidasi
  (atau embed kegagalan — tidak pernah keduanya)
- 🗃️ **Persistensi SQLite** — jadwal, riwayat eksekusi, metadata laporan;
  dipulihkan otomatis saat startup
- 📚 **Katalog laporan** — API + UI dengan unduhan Markdown/PDF berbasis id
  yang tervalidasi
- 🐳 **Fully dockerized** — satu perintah menyalakan seluruh stack; volume
  menjaga semua data lintas restart

---

## Cara Pakai

### 1. Riset manual (interaktif)

1. Buka **http://localhost:3000**
2. Ketik pertanyaan riset (Bahasa Indonesia juga bisa — mis.
   *"Apa berita terbaru dunia AI minggu ini?"*)
3. Klik jalankan riset lengkap — pipeline berjalan: rencana → query
   (otomatis English untuk kualitas pencarian) → cari web → indeks →
   sintesis → laporan
4. Pantau tiap tahap di panel (sumber, RAG, sintesis), lalu unduh laporan
   **Markdown** atau **PDF** dari katalog laporan

Setiap tahap juga bisa dipanggil terpisah lewat API (lihat
[Referensi API](#referensi-api)) — misal hanya pencarian sumber tanpa sintesis.

### 2. Riset terjadwal (otomatis)

Buat jadwal dari panel **Penjadwal** di UI atau lewat API:

```json
POST /api/research/schedules
{
  "question": "Apa berita dan perkembangan terbaru dunia AI minggu ini?",
  "schedule_type": "cron",
  "cron_expression": "0 8 * * 1",
  "timezone": "Asia/Jakarta"
}
```

Contoh di atas = ringkasan berita AI **setiap Senin 08:00 WIB**. Tersedia
juga mode interval (`every N minutes`, 5 menit … 1 tahun). Jadwal bisa
dijeda, dilanjutkan, dijalankan manual kapan pun, atau dihapus — semua
persisten dan selamat dari restart.

### 3. Hasil riset ke Discord

Isi `DISCORD_WEBHOOK_URL` di `backend/.env`, dan setiap eksekusi (manual
atau terjadwal) otomatis mengirim satu embed ke channel-mu berisi:

- 📊 **Statistik** — jumlah sumber & bukti
- 🔬 **Ringkasan** riset
- 📌 **Temuan utama** dalam bullet
- 🔗 **Sitasi tervalidasi** `[E1] [E2] …` — hanya sitasi yang lolos
  validasi server-side

PDF/Markdown **tidak di-attach** ke Discord — laporan lengkap tetap
tersedia di katalog web app.

---

## Arsitektur

```
Pengguna
 ↓
Next.js (frontend, :3000)
 ↓  HTTP (JSON)
FastAPI (backend, :8010)
 ↓
Research Planner (GLM) → Query Generator (GLM + fallback deterministik)
 ↓
Pencarian Web (ddgs — gratis)
 ↓
Ekstraksi Konten (httpx, penjaga SSRF, berbatas) → Pembersihan Teks (BeautifulSoup)
 ↓
Chunking (deterministik) → Embedding (Sentence Transformers lokal)
 ↓
ChromaDB (vector store lokal persisten, data/chroma/)
 ↓
Retrieval (top-k, metadata lengkap)
 ↓
Sintesis GLM (bukti dinomori [E1]…[En] sebelum dilihat model)
 ↓
Validasi Sitasi (server-side, deterministik — URL buatan LLM tidak pernah dipercaya)
 ↓
Generator Laporan (Markdown + ReportLab PDF → reports/)
 ↓
SQLite (data/researchflow.db — jadwal, eksekusi, metadata laporan)
 ↓
APScheduler (runtime engine in-process, dipulihkan dari SQLite saat startup)
 ↓
Webhook Discord (satu embed per eksekusi, secret khusus backend)
```

Pengelompokan di dalam backend bersifat ketat:

```
API (FastAPI) → Services → Repositories → SQLAlchemy 2.x → SQLite
```

Layer API tidak pernah menyentuh ORM langsung, service tidak pernah
bocor SQL, dan setiap kegagalan persistensi dipetakan menjadi error
aman-klien (HTTP 503) dengan penyebab asli hanya untuk log server.

### Teknologi

| Lapisan       | Teknologi |
| ------------- | --------- |
| Frontend      | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4 |
| Backend       | Python 3.14, FastAPI, Uvicorn |
| LLM           | GLM API via endpoint kompatibel-Anthropic milik Z.ai (**satu-satunya API berbayar**) |
| Pencarian web | `ddgs` (gratis, tanpa API key) di belakang `SearchProvider` yang bisa ditukar |
| Embedding     | Sentence Transformers `all-MiniLM-L6-v2` (lokal, gratis) |
| Vector DB     | ChromaDB (store lokal persisten, ruang cosine) |
| Database      | SQLite + SQLAlchemy 2.x (mode WAL, datetime naive-UTC) |
| Penjadwal     | APScheduler 3.x (AsyncIOScheduler, in-process) |
| Laporan       | Markdown (renderer deterministik) + ReportLab PDF (penuh offline) |
| Notifikasi    | Discord incoming webhook via httpx (tanpa bot, tanpa OAuth, tanpa SDK) |
| Kontainer     | Docker + Docker Compose |

---

## Cara Kerja Pipeline

Satu eksekusi riset — interaktif (`POST /api/research/report`) maupun
terjadwal — selalu mengikuti delapan tahap yang sama, masing-masing tepat sekali:

1. **Rencana** — GLM menguraikan pertanyaan menjadi rencana riset.
2. **Query** — GLM mengusulkan 3–5 query pencarian beragam; fallback
   deterministik menyusun query dari pertanyaan bila GLM tidak tersedia,
   sehingga pencarian selalu bisa jalan.
3. **Pencarian** — query menyebar ke provider ddgs gratis; hasil digabung,
   dideduplikasi berdasar URL ternormalisasi, dan dibatasi jumlahnya.
   Kegagalan query individual tidak pernah menggagalkan request; hanya
   kegagalan total yang gagal.
4. **Ekstraksi & pembersihan** — tiap halaman sumber di-fetch (timeout
   15 detik, batas 5 MB / 100 ribu karakter, penjaga SSRF) lalu
   direduksi menjadi teks yang terbaca.
5. **Indeks** — teks bersih di-chunk deterministik, di-embed lokal, dan
   di-upsert ke ChromaDB (id `sha256(url + chunk_index)` → indeks ulang
   mengganti, bukan menduplikasi). Situs yang gagal dihitung, tidak pernah fatal.
6. **Retrieval** — pertanyaan di-embed dengan model yang sama; top-k chunk
   kembali lengkap dengan metadata sumbernya.
7. **Sintesis** — bukti dinomori `[E1]…[En]`, GLM menulis jawaban
   ber-grounding, dan setiap sitasi divalidasi ulang di server (lihat
   [Sistem Sitasi](#sistem-sitasi)).
8. **Laporan** — sintesis tervalidasi dirender sekali sebagai Markdown dan
   sekali sebagai PDF; metadata masuk SQLite; file mendarat di `reports/`.

### Pipeline RAG

```
Sumber Web (metadata dari pencarian)
    ↓  Ekstraksi Konten (httpx · penjaga SSRF · batas ukuran · tanpa redirect)
Pembersihan Teks (BeautifulSoup — noise script/style/nav dibuang)
    ↓
Chunking Deterministik (1.000 karakter · overlap 150 · aman batas kata)
    ↓
Embedding Lokal (all-MiniLM-L6-v2 — dimuat sekali per proses)
    ↓
Upsert ChromaDB (ruang cosine · id chunk deterministik · idempoten)
    ↓
Retrieval Semantik (top-k 1–20 · URL/judul/domain sumber + jarak)
```

Catatan desain:

- **Indeks idempoten.** Id chunk diturunkan dari URL ternormalisasi dan
  indeks chunk, sehingga mengindeks artikel yang sama dua kali hanya
  mengganti chunk-nya — basis pengetahuan tidak bisa membengkak oleh duplikat.
- **Kegagalan parsial adalah data, bukan error.** `failed_sources`
  menghitung situs yang gagal di-fetch/diparse; hanya kegagalan
  infrastruktur (model embedding, ChromaDB) yang muncul sebagai 503.
- **Vector store append-only dari sudut pandang API** — satu-satunya jalur
  tulis adalah pengindeksan dan upsert; tidak ada penghapusan sembarangan.

### Sintesis AI

`POST /api/research/synthesize` (atau logika yang sama di dalam eksekusi apa pun):

1. Retrieve top-k chunk bukti untuk pertanyaan.
2. Nomori `[E1]…[En]` dan susun konteks bukti — **sebelum** model dipanggil.
3. Minta GLM menjawab dengan sitasi bukti inline, di bawah system prompt
   yang melarang mengikuti instruksi yang ditemukan di dalam bukti
   (pertahanan prompt-injection — lihat [Keamanan](#keamanan)).
4. Parse dan validasi setiap sitasi di respons (bagian berikutnya).
5. Retry sekali bila jawaban salah format atau menyitir bukti yang tidak
   ada; kegagalan kedua mengembalikan status yang jujur, bukan jawaban karangan:
   - `success` — jawaban ber-grounding, sitasi lengkap
   - `insufficient_evidence` — basis pengetahuan terlalu tipis untuk menjawab
   - `ungrounded` — model tidak bisa tetap pada bukti

### Sistem Sitasi

Integritas sitasi ditegakkan oleh backend, secara deterministik:

- Id bukti `[E1]…[En]` ditetapkan **di sisi server** sebelum generasi,
  sehingga sitasi hanya bisa merujuk sesuatu yang benar-benar ter-retrieve.
- Setelah generasi, tiap `[En]` di jawaban diperiksa terhadap himpunan
  bukti yang ter-retrieve. Referensi tak dikenal, kurung siku salah bentuk,
  atau jawaban tanpa sitasi padahal bukti ada → retry, lalu tolak.
- Pemetaan sitasi → sumber (URL, judul, domain, indeks chunk) dibangun
  **hanya dari metadata backend**. Klaim model sendiri tentang URL atau
  sumber tidak pernah dipercaya, digema, atau disimpan.
- Jawaban final terkirim bersama daftar sumber yang bisa dibuktikan
  backend cocok dengan setiap sitasi di teks.

### Pembuatan Laporan

`POST /api/research/report` menjalankan sintesis tervalidasi sekali, lalu render:

- **Markdown** — renderer deterministik (input sama ⇒ output sama),
  dengan daftar sumber lengkap.
- **PDF** — ReportLab, penuh offline (tanpa font/gambar/resource remote;
  teks hostil/XML-unsafe di-escape dan tetap selamat).

Unduhan hanya memakai **id laporan tervalidasi** — backend me-resolve id
terhadap metadata SQLite, merekonstruksi nama file persis yang
diharapkan, dan menyajikan file dari `reports/`. Tidak ada path dari
klien, tidak ada traversal.

- `GET /api/research/reports/{id}/markdown`
- `GET /api/research/reports/{id}/pdf`
- `GET /api/research/reports?limit=1..100` — katalog, terbaru dulu

Setiap laporan — interaktif atau terjadwal — mendapat satu baris metadata
di SQLite (query, nama file, status sintesis, jadwal/eksekusi asal).
File-nya sendiri tidak pernah diduplikasi ke database.

---

## Penjadwal

Pertanyaan apa pun bisa menjadi job berulang:

- **Interval** (`every N minutes`, 5 menit … 1 tahun) atau **cron**
  (ekspresi lima field, hanya di-parse `CronTrigger` APScheduler — tidak
  pernah dieksekusi, tidak pernah diberikan ke shell).
- **Zona waktu IANA** (default `Asia/Jakarta`); jadwal berikutnya dihitung
  dalam zona waktu jadwal dan disimpan sebagai UTC.
- **Pause / resume / jalankan sekarang / hapus**, semua langsung persisten.
- **Aman konkuren:** flag berjalan per jadwal + APScheduler
  `max_instances=1` + semaphore global + batas frekuensi minimum 5 menit +
  batas registri 50 jadwal.
- **Kegagalan tidak pernah mematikan scheduler** — eksekusi gagal mencatat
  error aman-klien dan jadwal tetap hidup.

Endpoint (`/api/research/schedules`):

| Metode   | Path                                       | Efek                                          |
| -------- | ------------------------------------------ | --------------------------------------------- |
| `POST`   | `/api/research/schedules`                  | Buat (divalidasi, persisten, terdaftar)       |
| `GET`    | `/api/research/schedules`                  | Daftar semua                                  |
| `GET`    | `/api/research/schedules/{id}`             | Lihat satu                                    |
| `POST`   | `/api/research/schedules/{id}/pause`       | Nonaktifkan (persisten)                       |
| `POST`   | `/api/research/schedules/{id}/resume`      | Aktifkan lagi (persisten)                     |
| `POST`   | `/api/research/schedules/{id}/run`         | Jalankan sekarang (202; jadwal tak tersentuh) |
| `GET`    | `/api/research/schedules/{id}/executions`  | Riwayat eksekusi, terbaru dulu (1–100)        |
| `DELETE` | `/api/research/schedules/{id}`             | Hapus (persisten)                             |

---

## Notifikasi Discord

- Satu embed **per eksekusi**, dikirim **setelah** hasil diketahui —
  sukses atau gagal, tidak pernah keduanya, tidak pernah status antah-berantah.
- **Embed sukses membawa hasil risetnya sendiri**: ringkasan, temuan
  utama (dinormalisasi ke bullet), statistik (sumber ditemukan · chunk
  bukti), dan **sitasi tervalidasi** — id bukti dengan tautan
  judul/domain sumber yang diambil HANYA dari record sitasi yang sudah
  divalidasi pipeline sintesis terhadap metadata sumber ter-retrieve,
  bukan dari keluaran LLM bebas. Kontennya diturunkan ulang dari outcome
  sintesis yang sama dengan laporan — tanpa riset ulang, tanpa pipeline
  kedua, satu pengiriman per eksekusi.
- **Tanpa attachment**: laporan Markdown/PDF tidak pernah diposting ke
  Discord. Embed hanya menyebut nama file laporan; laporan lengkap tetap
  bisa diunduh dari web app (Katalog Laporan).
- Konten aman terhadap batas ukuran Discord: budget per-field dan
  per-embed dengan penanda overflow eksplisit `+N lainnya` — daftar
  temuan/sitasi panjang dipotong secara terlihat, tidak pernah diam-diam.
- URL webhook adalah **secret khusus backend**: validasi ketat (HTTPS,
  host `discord.com`/`discordapp.com`, path `/api/webhooks/`, tanpa
  port/userinfo/dot-segment — gagal-tutup ke `not_configured`), tidak
  pernah diekspos ke frontend, tidak pernah di respons API.
- `allowed_mentions: {"parse": []}` mematikan payload-injection lewat
  mention; panjang field dibatasi; error aman-klien.
- Pengiriman: httpx, timeout 8 detik, maks 2 percobaan, tanpa retry pada 4xx.
- Status notifikasi (`not_configured` / `sent` / `failed`) disimpan per
  jadwal **dan** per eksekusi, sengaja terpisah dari status riset —
  kegagalan Discord tidak pernah menandai riset gagal.
- `GET /api/research/notifications/status` → hanya boolean (tanpa URL).

---

## Persistensi Data

**SQLite adalah sumber kebenaran; APScheduler hanyalah runtime engine.**

- Database: `data/researchflow.db` (di-gitignore; path via `DATABASE_URL`).
- Tabel: `schedules`, `research_executions`, `reports` (hanya metadata —
  tidak pernah isi file, tidak pernah secret).
- **Startup:** init/buat tabel → muat jadwal → validasi ulang tiap
  definisi tersimpan (baris invalid dinonaktifkan dengan error tercatat
  dan tidak pernah merobek startup) → bangun ulang trigger → daftarkan
  jadwal **enabled** saja → start APScheduler.
- **Setiap aksi persisten:** buat, pause, resume, hapus, awal/hasil
  eksekusi, status notifikasi, metadata laporan — transaksi pendek,
  di-rollback saat gagal, mode WAL untuk baca konkuren.
- **Transaksi selalu pendek:** panggilan GLM, pencarian web, embedding,
  ChromaDB, rendering laporan, dan request Discord semua terjadi **di luar**
  transaksi database apa pun.
- **Aman restart:** shutdown sengaja menyimpan semua baris; boot
  berikutnya merekonstruksi state runtime dari SQLite saja (diverifikasi
  tes restart khusus dua-instance).
- Datetime disimpan sebagai naive UTC dan dipasang ulang ke UTC saat
  dibaca; zona waktu jadwal hanya memengaruhi komputasi trigger.

---

## Referensi API

Base URL: `http://localhost:8010` — dokumentasi interaktif lengkap di
`/docs` (Swagger UI dari skema OpenAPI).

| Metode | Path | Fungsi |
| ------ | ---- | ------ |
| `GET`  | `/health` | Liveness (dipakai healthcheck Docker) |
| `POST` | `/api/research/plan` | Rencana riset untuk satu pertanyaan (GLM) |
| `POST` | `/api/research/search` | Generate query + pencarian web gratis + dedup |
| `POST` | `/api/research/index` | Fetch/ekstraksi/chunk/embed/upsert sumber |
| `POST` | `/api/research/retrieve` | Retrieval semantik top-k dengan metadata |
| `POST` | `/api/research/synthesize` | Sintesis ber-grounding + sitasi tervalidasi |
| `POST` | `/api/research/report` | Sintesis sekali → Markdown + PDF |
| `GET`  | `/api/research/reports` | Katalog laporan (terbaru dulu, `limit` 1–100) |
| `GET`  | `/api/research/reports/{id}/markdown` | Unduh aman (id tervalidasi) |
| `GET`  | `/api/research/reports/{id}/pdf` | Unduh aman (id tervalidasi) |
| `POST` | `/api/research/schedules` | Buat riset terjadwal |
| `GET`  | `/api/research/schedules` | Daftar jadwal |
| `GET`  | `/api/research/schedules/{id}` | Lihat satu |
| `POST` | `/api/research/schedules/{id}/pause` | Jeda (persisten) |
| `POST` | `/api/research/schedules/{id}/resume` | Lanjutkan (persisten) |
| `POST` | `/api/research/schedules/{id}/run` | Jalankan manual (202, async) |
| `GET`  | `/api/research/schedules/{id}/executions` | Riwayat eksekusi (terbaru dulu) |
| `DELETE` | `/api/research/schedules/{id}` | Hapus (persisten) |
| `GET`  | `/api/research/notifications/status` | Discord terkonfigurasi? (hanya boolean) |

Semua error aman: `{"detail": "…"}` dengan pesan aman-klien — tidak
pernah stack trace, tidak pernah API key, tidak pernah path filesystem.

---

## Docker

```bash
docker compose up --build
```

- **Volume:** `./data` (SQLite + ChromaDB + cache model HF) dan
  `./reports` (laporan yang dihasilkan) di-bind-mount — restart dan
  rebuild kontainer tidak kehilangan apa pun.
- **Secret:** `backend/.env` (di-gitignore) masuk ke kontainer backend
  saat runtime hanya via `env_file`. `.dockerignore` menjaganya (beserta
  `.venv`, tests, cache) keluar dari kedua build context; tidak ada
  secret yang masuk layer image atau build frontend.
- **Image backend:** Python 3.14-slim, index wheel torch CPU-only, user
  non-root (uid 1000), healthcheck berbasis stdlib.
- **Image frontend:** Node 22, `npm ci` → `next build` → `next start`,
  dengan tepat satu build arg: `NEXT_PUBLIC_API_BASE_URL` publik.

---

## Struktur Proyek

<details>
<summary>Klik untuk melihat struktur direktori lengkap</summary>

```
ResearchFlow-AI/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── research.py        # plan/search/index/retrieve/synthesize/report + unduhan & katalog laporan
│   │   │   ├── schedules.py       # CRUD jadwal + pause/resume/run + riwayat eksekusi
│   │   │   └── notifications.py   # status notifikasi (hanya boolean)
│   │   ├── core/config.py         # konfigurasi env (GLM, CORS, path, zona waktu, webhook, database URL)
│   │   ├── database/
│   │   │   ├── database.py        # engine/session factory, helper naive-UTC
│   │   │   ├── models.py          # ScheduleRow / ExecutionRow / ReportRow
│   │   │   └── repositories.py    # repositori Schedule/Execution/Report
│   │   ├── schemas/               # model request/response Pydantic
│   │   │   ├── research.py · schedule.py · notification.py · report.py
│   │   ├── services/
│   │   │   ├── glm_service.py     # klien GLM (httpx, format Anthropic Messages)
│   │   │   ├── research_planner.py · query_generator.py
│   │   │   ├── search_provider.py · search_service.py
│   │   │   ├── content_extractor.py · text_cleaner.py
│   │   │   ├── chunking_service.py · embedding_service.py · vector_store.py
│   │   │   ├── rag_service.py     # orkestrasi indeks/retrieve
│   │   │   ├── citation_service.py · synthesis_service.py
│   │   │   ├── report_service.py · markdown_reporter.py · pdf_reporter.py
│   │   │   ├── research_execution_service.py  # satu eksekusi pipeline penuh
│   │   │   ├── scheduler_service.py           # APScheduler + registri ber-SQLite
│   │   │   ├── report_catalog_service.py      # metadata laporan di atas ReportRepository
│   │   │   └── discord_service.py             # validasi webhook + pengiriman
│   │   └── main.py                # entrypoint FastAPI (lifespan: init DB + scheduler)
│   ├── tests/                     # pytest — semua eksternal di-mock, SQLite sementara per tes
│   ├── Dockerfile · .dockerignore · pyproject.toml (konfigurasi ruff)
│   ├── requirements.txt · requirements-dev.txt
│   └── .env (di-gitignore) · .env.example
├── frontend/
│   ├── src/app/ · src/components/ # form, sumber, RAG, sintesis, penjadwal, laporan, status
│   ├── Dockerfile · .dockerignore
│   └── .env.local (di-gitignore)
├── data/                          # DB SQLite, ChromaDB, cache model HF (di-gitignore, volume Docker)
├── reports/                       # laporan .md/.pdf hasil generate (di-gitignore, volume Docker)
├── docker-compose.yml
└── README.md
```

</details>

---

## Pengujian & Pengembangan Lokal

### Backend

```bash
cd backend && source .venv/bin/activate
pytest                      # 478 tes
ruff check app tests        # 0 error
```

- **Semua eksternal di-mock**: GLM (tanpa panggilan berbayar), halaman
  web dan webhook Discord (httpx `MockTransport`), model Sentence
  Transformers, ChromaDB. Suite ini gratis dan tanpa jaringan.
- **Setiap tes berjalan di file SQLite sementara** (fixture autouse) —
  database produksi tidak pernah disentuh.
- **Persistensi restart diuji di level service**: dua instance scheduler
  atas satu database — jadwal enabled pulih sebagai job aktif, yang
  dipause tetap tak terdaftar, riwayat eksekusi dan metadata laporan selamat.
- Cakupan: health, planner, pencarian, ekstraksi (termasuk matriks
  SSRF), pembersihan, chunking, embedding, vector store, RAG, sintesis,
  sitasi, laporan (Markdown + PDF), scheduler, notifikasi, SQLite,
  persistensi restart, dan layer API setiap endpoint di atas.

### Frontend

```bash
cd frontend
npm run lint    # eslint (next/core-web-vitals)
npm run build   # build produksi
```

### Menjalankan mode dev

```bash
uvicorn app.main:app --port 8010 --reload   # backend :8010
npm run dev                                 # frontend :3000
```

---

## Keamanan

- **Secret** — `GLM_API_KEY` dan `DISCORD_WEBHOOK_URL` hanya ada di
  `backend/.env`, dibaca via environment variable: tidak pernah
  di-hardcode, tidak pernah di-log, tidak pernah disimpan di SQLite,
  tidak pernah dikirim ke browser, tidak pernah di respons/pesan error API apa pun.
- **SSRF** — URL sumber divalidasi sebelum di-fetch: hanya http/https,
  hanya port standar, tanpa kredensial tersemat, tanpa redirect (klien
  menolak mengikuti), target localhost/loopback/private/link-local/
  reserved/multicast ditolak — termasuk bentuk IP tanpa titik
  (`2130706433`, `0x7f.0.0.1`) yang luput dari parsing biasa. Webhook
  Discord divalidasi dengan cara gagal-tutup yang sama.
- **Prompt injection** — konten web yang diretrieve dibingkai sebagai
  data tidak terpercaya; system prompt melarang mengikuti instruksi di
  dalam bukti, dan sitasi divalidasi di server terlepas dari klaim model.
- **Keamanan file** — unduhan laporan me-resolve id tervalidasi terhadap
  metadata DB dan merekonstruksi nama file persis (cek containment
  `relative_to`); path traversal dan nama file sembarangan mustahil
  secara struktural. Nama file hasil generate diturunkan dari pertanyaan
  ter-slugify plus id aman.
- **Validasi input** — setiap skema Pydantic menegakkan panjang min/maks,
  enum, batas numerik (interval, limit, top-k), bentuk URL, dan
  validitas jadwal (tipe/ekspresi/zona waktu/jarak) sebelum kerja apa pun dimulai.
- **Penanganan error** — klien melihat pesan aman dan kode status benar
  (422/502/503/504); stack trace, SQL, path, dan penyebab tetap di log
  server (`from None` / exception berantai bila berguna).
- **Konkurensi** — penjaga per jadwal + `max_instances=1` + semaphore
  global mencegah eksekusi ganda/beririsan; SQLite berjalan mode WAL
  dengan transaksi pendek yang hanya mengunci sekitar tulis lokal cepat.
- **Tanpa `eval`/`exec`/shell** — ekspresi cron dan semua input lain
  di-parse oleh library, tidak pernah dieksekusi.

---

## Keterbatasan

Dinyatakan jujur — ini alat riset lokal/self-hosted, bukan SaaS terdistribusi:

- **SQLite bersifat lokal**, single-node, dan tidak ditujukan untuk
  produksi terdistribusi atau multi-writer.
- **APScheduler in-process**: satu proses backend menjalankan scheduler.
  Menjalankan beberapa replika backend atas database yang sama akan
  menjalankan jadwal ganda — deploy satu worker backend saja.
- **Tanpa scheduler terdistribusi** (tanpa Celery/Redis/broker) by design.
- **ddgs berbasis scraping** dan bisa rusak saat provider berubah;
  hasil kadang kosong atau terkena rate-limit.
- **Sebagian situs memblokir request otomatis** — kegagalan ekstraksi
  wajar dan dihitung (`failed_sources`), tidak pernah fatal.
- **GLM butuh API key** (satu-satunya dependensi berbayar); tanpanya,
  planning/sintesis mengembalikan 503 sementara pencarian tetap jalan
  lewat query fallback.
- **ChromaDB lokal** — tidak ada berbagi vektor lintas mesin.
- **Docker ditujukan untuk deployment lokal/self-hosted** (satu host,
  volume bind-mount), bukan produksi multi-node terorkestrasi.
- Penjaga SSRF memang setara-MVP (DNS rebinding di luar cakupan); alat
  ini mengambil halaman web publik, bukan jaringan internal.
- Pengiriman Discord adalah incoming webhook — hanya post, satu channel,
  tanpa perintah bot atau pembacaan.
- **UI dan output Indonesia, pencarian English**: UI, rencana riset,
  jawaban sintesis, laporan (MD/PDF), dan notifikasi Discord berbahasa
  Indonesia. Query pencarian sengaja di-generate dalam English karena
  mayoritas sumber web terindeks dan model embedding optimal untuk
  English — kualitas retrieval dalam bahasa lain lebih rendah. Pesan
  error yang menghadap klien berbahasa Indonesia; nilai enum API
  (`running`, `success`, `manual`, ...) dan log developer tetap English.

---

## Rencana Pengembangan

- Skor kredibilitas & prioritas sumber (reputasi domain, kebaruan saat
  tanggal tersedia)
- Ingesti sumber PDF/paper langsung (PyMuPDF) di samping halaman HTML
- Tahap reranking antara retrieval dan sintesis (mis. cross-encoder)
- Sesi riset multi-pertanyaan dan pertanyaan lanjutan
- Channel Discord / filter notifikasi per jadwal
- Format ekspor selain Markdown + PDF (DOCX, BibTeX)
- Cache retrieval dan indeks ulang inkremental berbasis hash konten
- Autentikasi opsional untuk frontend/API (saat ini trust-local)
- Jalur migrasi SQLite → Postgres bila kebutuhan multi-writer muncul

---

*Dibangun sebagai proyek portofolio: satu API LLM berbayar (GLM),
sisanya gratis dan lokal.*
