# SpecBridge Agent

Backend Python/FastAPI + Google ADK untuk mengubah normalized notes menjadi draft
PRD/SRS dan RTM yang traceable. Frontend Next.js berada di project `../specbridge`
dan memakai proxy server-side agar API key backend tidak masuk ke bundle browser.

## Workflow MVP

Alur root ADK bersifat eksplisit dan bounded:

1. Extractor membuat kandidat requirement beserta kutipan sumber.
2. Trace Linker membandingkan kandidat dengan requirement lama secara project-scoped.
3. BA, PM, Developer, QA, dan Designer melakukan review ronde pertama secara paralel.
4. Kelima role melakukan satu ronde rebuttal secara paralel.
5. Moderator menyimpulkan consensus, unresolved dissent, atau kebutuhan context manusia.
6. Conflict Detector mencatat konflik tanpa menyelesaikannya.
7. Document Writer membuat draft PRD/SRS dan RTM dengan `publication_ready=false`.

Product Owner tetap wajib memutuskan konflik dan mempublikasikan dokumen. Requirement
manual manusia disimpan sebagai revision immutable dengan provenance `human_manual`.

## Menjalankan lokal

Prasyarat: Python 3.11–3.13, `uv`, `google-agents-cli` 1.11.x, dan kredensial
Vertex AI bila ingin menjalankan model live.

```bash
cp .env.example .env
make install
make demo
```

Isi `.env` dengan URL PostgreSQL/Neon dan API key lokal. Jangan commit `.env`.
Input API dapat berupa JSON hasil normalisasi atau upload langsung PDF, DOCX, TXT,
dan Markdown. File dinormalisasi secara lokal sebelum masuk ke workflow agent. PDF
hasil scan/gambar belum memakai OCR dan akan ditolak dengan pesan yang eksplisit.

Untuk smoke test end-to-end sementara tanpa integrasi frontend Next.js, buka
`http://localhost:8000/demo`. Isi API key lokal, edit normalized input JSON, lalu
klik **Run end-to-end**. Halaman ini menjalankan workflow Vertex AI yang sebenarnya
dan menyimpan project, source, run, findings, conflicts, draft, serta RTM ke Neon.
API key hanya disimpan di memori halaman dan tidak dimasukkan ke URL/local storage.
Jika `DATABASE_URL` belum ada di `.env`, `make demo` akan meminta connection string
melalui input terminal tersembunyi. Jika `SPECBRIDGE_API_KEY` belum ada, command akan
membuat dan menampilkan API key lokal sementara setelah migrasi berhasil.
Default workflow timeout adalah 270 detik agar bounded debate tetap berada di bawah
request timeout Cloud Run 300 detik. Export telemetry Cloud dinonaktifkan sampai role
observability service account sengaja dikonfigurasi.
Runner demo lokal memakai timeout 360 detik agar satu retry structured-output masih
memiliki ruang. Setiap node LLM hanya mengulang sekali dan hanya jika validasi JSON
Pydantic gagal; error permission, kebijakan, atau bisnis tidak diulang otomatis.
Output agent dibatasi oleh instruksi ringkas dengan headroom maksimum 8.192 token;
headroom ini mencegah JSON reviewer terpotong dan bukan target pemakaian token.

## API

`GET /health` dan UI statis `GET /demo` bersifat publik. Semua endpoint eksekusi—
termasuk endpoint native ADK dan A2A—memerlukan header `X-API-Key` agar deployment
Cloud Run publik tidak membuka akses model tanpa kontrol.

Endpoint utama:

- `POST /v1/projects`
- `GET /v1/projects`
- `GET /v1/projects/{project_id}/workspace`
- `POST /v1/projects/{project_id}/sources`
- `POST /v1/projects/{project_id}/uploads`
- `POST /v1/projects/{project_id}/requirements/manual`
- `PATCH /v1/projects/{project_id}/requirements/{requirement_id}`
- `PATCH /v1/projects/{project_id}/requirements/{requirement_id}/review`
- `PATCH /v1/projects/{project_id}/requirements/{requirement_id}/implementation-status`
- `POST /v1/projects/{project_id}/analysis`
- `GET /v1/projects/{project_id}/analysis/{run_id}`
- `POST /v1/conflicts/{record_id}/decisions|defer|comments`
- `POST /v1/documents/{document_id}/publish|request-changes`

Untuk menjalankan frontend dan backend bersama dari root workspace:

```bash
cd ..
make dev
```

Command tersebut membaca `specbridge-agent/.env`, menjalankan migration, lalu membuka
FastAPI di port 8000 dan Next.js di port 3000 dalam backend mode. Gunakan Node 20
(`nvm use` dari folder frontend); Next.js 13 di repo ini tidak mendukung Node 24.

Contoh membuat project:

```bash
curl -X POST http://localhost:8000/v1/projects \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $SPECBRIDGE_API_KEY" \
  -d '{"project_id":"demo","name":"Demo","document_type":"PRD"}'
```

Contoh analisis normalized note:

```bash
curl -X POST http://localhost:8000/v1/projects/demo/analysis \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $SPECBRIDGE_API_KEY" \
  -d '{
    "project_id":"demo",
    "document_type":"PRD",
    "sources":[{
      "source_id":"po-note-001",
      "source_type":"manual_note",
      "title":"Catatan Product Owner",
      "content":"Pengguna harus dapat melihat status pengajuan."
    }]
  }'
```

Response analysis menyertakan `resources.document_id` dan mapping external conflict
ID ke UUID database yang dipakai endpoint keputusan manusia.

## Validasi dan evaluasi

```bash
make lint
make test
make eval
```

`make test` hanya memeriksa kontrak deterministik. Perilaku LLM dinilai lewat dua
kasus awal di `tests/eval/datasets/basic-dataset.json`: catatan kasar yang ambigu dan
transcript dengan konflik timeout. Evaluasi memerlukan Vertex AI/ADC dan membuat
sejumlah model call; hasil angka, bukan exit code saja, menjadi deployment gate.

## Neon dan migrasi

Migration awal mengaktifkan extension `vector` lalu membuat tabel project, policy,
source, revision requirement, trace, findings, conflicts, documents, runs, dan audit.

```bash
DATABASE_URL='postgresql://...' make migrate
```

Connection string database yang pernah dibagikan selama development harus dirotasi
sebelum deployment. Simpan URL baru dan API key di Secret Manager, bukan source code:

```bash
gcloud secrets create specbridge-database-url --replication-policy=automatic
gcloud secrets create specbridge-api-key --replication-policy=automatic
```

Tambahkan secret version melalui input tersembunyi atau pipeline aman organisasi;
jangan menaruh nilainya di command history.

## Cloud Run

Deployment default memakai `asia-southeast1`, endpoint model `global`, scale-to-zero,
1 vCPU, 2 GiB RAM, concurrency 2, dan maksimum 3 instance. Service Cloud Run boleh
publik untuk MVP, tetapi aplikasi tetap memverifikasi API key.

Setelah login `gcloud`, memilih project, mengisi kedua secret, dan memastikan runtime
service account mempunyai `Vertex AI User` serta `Secret Manager Secret Accessor`:

```bash
make dry-run GCP_PROJECT=my-project
make deploy GCP_PROJECT=my-project
```

`make deploy` menjalankan lint, test, eval, migrasi Neon, lalu meminta alur deploy
Agents CLI. Jalankan hanya setelah skor eval disetujui. Untuk development tanpa auth,
`DISABLE_API_KEY_AUTH=true` hanya boleh dipakai lokal/eval dan tidak boleh dipasang di
Cloud Run.
