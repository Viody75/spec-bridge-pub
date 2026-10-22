from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


DEMO_HTML = r"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SpecBridge E2E Demo</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090c13;
      --panel: #111722;
      --panel-2: #171f2d;
      --line: #293448;
      --text: #edf2f7;
      --muted: #9dacbf;
      --accent: #8b7cff;
      --accent-2: #5eead4;
      --danger: #fb7185;
      --warning: #fbbf24;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 15% 0%, #27204f 0, transparent 34rem),
        var(--bg);
      color: var(--text);
      font: 15px/1.55 Inter, ui-sans-serif, system-ui, sans-serif;
    }
    main { width: min(1180px, calc(100% - 32px)); margin: 40px auto 80px; }
    h1 { margin: 0 0 8px; font-size: clamp(28px, 5vw, 48px); letter-spacing: -0.04em; }
    h2 { margin: 0 0 14px; font-size: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    p { margin: 0; }
    .muted { color: var(--muted); }
    .hero { margin-bottom: 24px; }
    .badge {
      display: inline-flex; align-items: center; gap: 8px; margin-bottom: 14px;
      padding: 5px 10px; border: 1px solid var(--line); border-radius: 999px;
      color: var(--accent-2); background: #101b20;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
    .dot.ok { background: var(--accent-2); box-shadow: 0 0 12px var(--accent-2); }
    .dot.bad { background: var(--danger); }
    .grid { display: grid; grid-template-columns: minmax(320px, .85fr) minmax(0, 1.4fr); gap: 20px; }
    .panel { background: color-mix(in srgb, var(--panel) 94%, transparent); border: 1px solid var(--line); border-radius: 16px; padding: 20px; }
    .sticky { position: sticky; top: 20px; align-self: start; }
    label { display: block; margin: 14px 0 6px; color: var(--muted); font-weight: 650; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea {
      width: 100%; color: var(--text); background: #0b111b; border: 1px solid var(--line);
      border-radius: 10px; padding: 10px 12px; outline: none;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px #8b7cff25; }
    textarea { min-height: 360px; resize: vertical; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }
    .row { display: grid; grid-template-columns: 1fr 120px; gap: 10px; }
    button {
      width: 100%; margin-top: 16px; border: 0; border-radius: 10px; padding: 11px 14px;
      cursor: pointer; color: white; font-weight: 750;
      background: linear-gradient(135deg, var(--accent), #5b8cff);
    }
    button:disabled { cursor: wait; opacity: .55; }
    .notice { margin-top: 12px; color: var(--warning); font-size: 13px; }
    .status { min-height: 56px; display: flex; align-items: center; gap: 10px; }
    .status.error { color: var(--danger); }
    .empty { padding: 56px 18px; color: var(--muted); text-align: center; border: 1px dashed var(--line); border-radius: 12px; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }
    .metric, .card { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
    .metric strong { display: block; font-size: 20px; }
    .metric span { color: var(--muted); font-size: 12px; }
    section + section { margin-top: 22px; }
    .stack { display: grid; gap: 10px; }
    .meta { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
    .pill { padding: 3px 8px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: 11px; }
    .quote { margin-top: 9px; padding-left: 10px; border-left: 2px solid var(--accent); color: #cbd5e1; }
    details { border: 1px solid var(--line); border-radius: 10px; background: #0b111b; }
    summary { cursor: pointer; padding: 10px 12px; font-weight: 700; }
    pre { margin: 0; padding: 14px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: #c7d2fe; font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); }
    @media (max-width: 850px) {
      .grid { grid-template-columns: 1fr; }
      .sticky { position: static; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <main>
    <header class="hero">
      <div class="badge"><span id="health-dot" class="dot"></span><span id="health-text">Memeriksa backend…</span></div>
      <h1>SpecBridge E2E Demo</h1>
      <p class="muted">Normalized notes → Google ADK multi-agent → Neon → draft PRD/SRS + RTM.</p>
    </header>

    <div class="grid">
      <aside class="panel sticky">
        <h2>Jalankan analisis nyata</h2>
        <p class="muted">Halaman ini sementara dan terpisah dari frontend Next.js.</p>

        <label for="api-key">API key lokal</label>
        <input id="api-key" type="password" autocomplete="off" placeholder="X-API-Key">

        <div class="row">
          <div>
            <label for="project-name">Nama project</label>
            <input id="project-name" value="SpecBridge E2E Demo">
          </div>
          <div>
            <label for="document-type">Dokumen</label>
            <select id="document-type"><option>PRD</option><option>SRS</option></select>
          </div>
        </div>

        <label for="payload">Normalized input JSON</label>
        <textarea id="payload" spellcheck="false">{
  "project_id": "specbridge-e2e-demo",
  "document_type": "PRD",
  "sources": [
    {
      "source_id": "po-note-001",
      "source_type": "manual_note",
      "title": "Catatan kasar Product Owner",
      "author": "Product Owner",
      "content": "Pengguna harus dapat mengajukan permintaan pembelian dan melihat statusnya. Manajer menyetujui permintaan bernilai tinggi, tetapi batas nilai tinggi belum disepakati. Tim operasional meminta riwayat keputusan dapat diaudit. Notifikasi diperlukan, namun kanal dan target waktunya belum diputuskan.",
      "metadata": {"normalization_version": "demo-v1"}
    }
  ]
}</textarea>

        <button id="run" type="button">Run end-to-end</button>
        <p class="notice">Menjalankan model Vertex AI dan menyimpan hasil test ke Neon. Proses dapat memerlukan beberapa menit.</p>
      </aside>

      <article class="panel">
        <div id="status" class="status"><span class="muted">Belum ada run.</span></div>
        <div id="result" class="empty">Hasil extraction, debate, moderation, conflict, draft, dan RTM akan tampil di sini.</div>
      </article>
    </div>
  </main>

  <script>
    const el = (id) => document.getElementById(id);
    const resultRoot = el("result");
    const statusRoot = el("status");

    function node(tag, text, className) {
      const item = document.createElement(tag);
      if (text !== undefined && text !== null) item.textContent = String(text);
      if (className) item.className = className;
      return item;
    }

    function setStatus(message, error = false) {
      statusRoot.replaceChildren(node("span", message));
      statusRoot.className = error ? "status error" : "status";
    }

    async function request(path, apiKey, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
          ...(options.headers || {})
        }
      });
      let body;
      try { body = await response.json(); }
      catch { body = {detail: await response.text()}; }
      return {response, body};
    }

    function appendHeading(parent, title) {
      parent.appendChild(node("h2", title));
    }

    function appendList(parent, values) {
      if (!values || values.length === 0) {
        parent.appendChild(node("p", "Tidak ada.", "muted"));
        return;
      }
      const list = node("div", null, "stack");
      values.forEach((value) => list.appendChild(node("div", value, "card")));
      parent.appendChild(list);
    }

    function render(data) {
      resultRoot.className = "";
      resultRoot.replaceChildren();

      const metrics = node("div", null, "metrics");
      const values = [
        [data.status, "status"],
        [data.extraction.requirements.length, "requirements"],
        [data.conflicts.conflicts.length, "conflicts"],
        [data.usage ? data.usage.model_calls : "—", "model calls"]
      ];
      values.forEach(([value, label]) => {
        const card = node("div", null, "metric");
        card.append(node("strong", value), node("span", label));
        metrics.appendChild(card);
      });
      resultRoot.appendChild(metrics);

      const reqSection = node("section");
      appendHeading(reqSection, "1. Extracted requirements");
      const reqStack = node("div", null, "stack");
      data.extraction.requirements.forEach((req) => {
        const card = node("div", null, "card");
        card.append(node("h3", `${req.candidate_id} · ${req.statement}`), node("p", req.rationale, "muted"));
        const meta = node("div", null, "meta");
        [req.kind, req.priority, req.source_basis, `confidence ${req.confidence}`].forEach((v) => meta.appendChild(node("span", v, "pill")));
        card.appendChild(meta);
        (req.evidence_refs || []).forEach((evidence) => card.appendChild(node("div", `${evidence.source_id}: “${evidence.quote}”`, "quote")));
        reqStack.appendChild(card);
      });
      reqSection.appendChild(reqStack);
      resultRoot.appendChild(reqSection);

      const reviewSection = node("section");
      appendHeading(reviewSection, "2. Structured bounded debate");
      const reviewStack = node("div", null, "stack");
      [...data.initial_reviews, ...data.rebuttals].forEach((review) => {
        const details = node("details");
        const counts = review.findings.reduce((acc, item) => { acc[item.verdict] = (acc[item.verdict] || 0) + 1; return acc; }, {});
        details.appendChild(node("summary", `${review.role} · round ${review.round} · ${review.findings.length} findings · ${JSON.stringify(counts)}`));
        const content = node("div", null, "stack");
        content.style.padding = "0 12px 12px";
        content.appendChild(node("p", review.overall_rationale, "muted"));
        review.findings.forEach((finding) => {
          const item = node("div", null, "card");
          item.append(node("h3", `${finding.finding_id} · ${finding.verdict}/${finding.severity}`), node("p", finding.claim), node("p", finding.rationale, "muted"));
          content.appendChild(item);
        });
        details.appendChild(content);
        reviewStack.appendChild(details);
      });
      reviewSection.appendChild(reviewStack);
      resultRoot.appendChild(reviewSection);

      const moderation = node("section");
      appendHeading(moderation, "3. Moderator");
      const modCard = node("div", null, "card");
      modCard.append(node("h3", data.moderation.status), node("p", data.moderation.rationale));
      if (data.moderation.human_questions.length) {
        modCard.appendChild(node("p", "Pertanyaan untuk manusia", "muted"));
        appendList(modCard, data.moderation.human_questions);
      }
      moderation.appendChild(modCard);
      resultRoot.appendChild(moderation);

      const conflictSection = node("section");
      appendHeading(conflictSection, "4. Conflicts untuk keputusan PO");
      const conflictStack = node("div", null, "stack");
      if (!data.conflicts.conflicts.length) conflictStack.appendChild(node("p", "Tidak ada konflik terdeteksi.", "muted"));
      data.conflicts.conflicts.forEach((conflict) => {
        const persisted = (data.resources?.conflicts || []).find((item) => item.conflict_id === conflict.conflict_id);
        const card = node("div", null, "card");
        card.append(node("h3", `${conflict.conflict_id} · ${conflict.title}`), node("p", conflict.rationale));
        const meta = node("div", null, "meta");
        meta.append(node("span", conflict.severity, "pill"), node("span", `DB: ${persisted?.record_id || "—"}`, "pill"));
        card.appendChild(meta);
        conflictStack.appendChild(card);
      });
      conflictSection.appendChild(conflictStack);
      resultRoot.appendChild(conflictSection);

      const docSection = node("section");
      appendHeading(docSection, `5. Draft ${data.document.document_type}: ${data.document.title}`);
      const docStack = node("div", null, "stack");
      data.document.sections.forEach((section) => {
        const card = node("div", null, "card");
        card.append(node("h3", section.title), node("p", section.content));
        docStack.appendChild(card);
      });
      docSection.appendChild(docStack);
      resultRoot.appendChild(docSection);

      const rtmSection = node("section");
      appendHeading(rtmSection, "6. Requirements Traceability Matrix");
      const table = node("table");
      const head = node("thead");
      const headerRow = node("tr");
      ["ID", "Requirement", "Sources", "Review", "Verification"].forEach((value) => headerRow.appendChild(node("th", value)));
      head.appendChild(headerRow);
      table.appendChild(head);
      const body = node("tbody");
      data.document.rtm.forEach((row) => {
        const tr = node("tr");
        [row.candidate_id, row.requirement_statement, row.source_ids.join(", "), row.review_status, row.verification_basis].forEach((value) => tr.appendChild(node("td", value)));
        body.appendChild(tr);
      });
      table.appendChild(body);
      rtmSection.appendChild(table);
      resultRoot.appendChild(rtmSection);

      const rawSection = node("section");
      const details = node("details");
      details.append(node("summary", "Raw JSON response"), node("pre", JSON.stringify(data, null, 2)));
      rawSection.appendChild(details);
      resultRoot.appendChild(rawSection);
    }

    async function checkHealth() {
      try {
        const response = await fetch("/health");
        const health = await response.json();
        el("health-dot").className = health.status === "ok" ? "dot ok" : "dot bad";
        el("health-text").textContent = `Backend ${health.status} · database ${health.database_configured ? "connected" : "not connected"}`;
      } catch {
        el("health-dot").className = "dot bad";
        el("health-text").textContent = "Backend tidak dapat dihubungi";
      }
    }

    el("document-type").addEventListener("change", () => {
      try {
        const payload = JSON.parse(el("payload").value);
        payload.document_type = el("document-type").value;
        el("payload").value = JSON.stringify(payload, null, 2);
      } catch { /* JSON validation occurs when Run is clicked. */ }
    });

    el("run").addEventListener("click", async () => {
      const button = el("run");
      const apiKey = el("api-key").value;
      if (!apiKey) { setStatus("API key wajib diisi.", true); return; }
      let payload;
      try { payload = JSON.parse(el("payload").value); }
      catch (error) { setStatus(`JSON tidak valid: ${error.message}`, true); return; }

      button.disabled = true;
      button.textContent = "Agents sedang bekerja…";
      resultRoot.className = "empty";
      resultRoot.textContent = "Extractor, Trace Linker, lima reviewer, rebuttal, moderator, conflict detector, dan document writer sedang diproses.";
      try {
        setStatus("Menyiapkan project di Neon…");
        const project = await request("/v1/projects", apiKey, {
          method: "POST",
          body: JSON.stringify({
            project_id: payload.project_id,
            name: el("project-name").value,
            document_type: payload.document_type,
            policy: {}
          })
        });
        if (!project.response.ok && project.response.status !== 409) {
          throw new Error(project.body.detail || `Project gagal (${project.response.status})`);
        }

        setStatus("Menjalankan workflow multi-agent. Jangan tutup halaman…");
        const analysis = await request(`/v1/projects/${encodeURIComponent(payload.project_id)}/analysis`, apiKey, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        if (!analysis.response.ok) throw new Error(analysis.body.detail || `Analysis gagal (${analysis.response.status})`);
        render(analysis.body);
        setStatus(`Selesai · run ${analysis.body.run_id} · hasil tersimpan di Neon`);
      } catch (error) {
        resultRoot.className = "empty";
        resultRoot.textContent = "Run tidak selesai. Detail aman ditampilkan di status di atas.";
        setStatus(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = "Run end-to-end";
        checkHealth();
      }
    });

    checkHealth();
  </script>
</body>
</html>
"""


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def demo() -> HTMLResponse:
    return HTMLResponse(
        content=DEMO_HTML,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
