/* ── Config ──
   Local dev: leave as-is, matches `python app.py` on port 5000/5050.
   Deployed: set window.API_BASE in config.js (loaded before this file)
   to your deployed backend URL, e.g. "https://fakejob-backend.onrender.com" */
const API_BASE = window.API_BASE || "http://localhost:5000";

/* ── Parameter definitions (mirrors backend) ── */
const CATS = [
  { cat: "Typographic & glyph", params: [
    { id:"glyph_sharpness",    name:"Glyph edge sharpness",        type:"std"  },
    { id:"interglyph_spacing", name:"Inter-glyph spacing variance", type:"std"  },
    { id:"baseline_jitter",    name:"Baseline jitter index",        type:"std"  },
    { id:"font_renderer",      name:"Font renderer consistency",    type:"std"  },
  ]},
  { cat: "Signature forensics", params: [
    { id:"ink_spread",   name:"Ink spread entropy",            type:"std" },
    { id:"edge_gaussian",name:"Edge Gaussian fit deviation",   type:"std" },
    { id:"dct_misalign", name:"DCT block misalignment index",  type:"std" },
    { id:"bg_texture",   name:"Background texture continuity", type:"std" },
  ]},
  { cat: "Temporal & metadata", params: [
    { id:"causal_inversion", name:"Causal inversion count",  type:"hard" },
    { id:"timezone_entropy", name:"Timezone entropy score",  type:"std"  },
    { id:"tool_anachronism", name:"Tool anachronism score",  type:"hard" },
  ]},
  { cat: "Visual & layout", params: [
    { id:"logo_compression",     name:"Logo compression lineage",       type:"std" },
    { id:"seal_anomaly",         name:"Seal frequency anomaly",         type:"std" },
    { id:"letterhead_deviation", name:"Letterhead structural deviation", type:"std" },
    { id:"color_profile",        name:"Color profile inconsistency",    type:"std" },
  ]},
  { cat: "Job posting authenticity", params: [
    { id:"domain_legitimacy",    name:"Domain legitimacy score",       type:"hard"  },
    { id:"urgency_language",     name:"Posting urgency language index", type:"boost" },
    { id:"contact_verifiability",name:"Contact verifiability score",   type:"std"   },
    { id:"cross_platform",       name:"Cross-platform consistency",    type:"std"   },
  ]},
];

const DEFAULT_WEIGHTS = {
  glyph_sharpness:6, interglyph_spacing:8, baseline_jitter:7, font_renderer:9,
  ink_spread:6, edge_gaussian:7, dct_misalign:9, bg_texture:8,
  causal_inversion:15, timezone_entropy:4, tool_anachronism:15,
  logo_compression:5, seal_anomaly:7, letterhead_deviation:9, color_profile:4,
  domain_legitimacy:15, urgency_language:10, contact_verifiability:9, cross_platform:5,
};

let weights = { ...DEFAULT_WEIGHTS };
let currentMode = "research";
let uploadedFile = null;
let radarChart = null;

/* ── Tab navigation ── */
document.querySelectorAll(".nav-item").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

/* ── Mode toggle ── */
function setMode(m) {
  currentMode = m;
  document.getElementById("modeResearch").classList.toggle("active", m === "research");
  document.getElementById("modePractical").classList.toggle("active", m === "practical");
}

/* ── File upload ── */
const dropZone = document.getElementById("dropZone");
dropZone.addEventListener("dragover", e => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => { e.preventDefault(); dropZone.classList.remove("drag-over"); if (e.dataTransfer.files[0]) processFile(e.dataTransfer.files[0]); });

function handleFile(e) { if (e.target.files[0]) processFile(e.target.files[0]); }
function processFile(f) {
  uploadedFile = f;
  const chip = document.getElementById("fileChip");
  chip.style.display = "inline-flex";
  chip.innerHTML = `<i class="ti ti-file-check"></i> ${f.name} <span style="color:var(--text-muted)">${(f.size/1024).toFixed(1)} KB</span>`;
}

/* ── Server health check ── */
async function checkServer() {
  try {
    const r = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(2000) });
    const ok = r.ok;
    document.querySelector(".status-dot").className = `status-dot ${ok ? "ok" : "err"}`;
    document.getElementById("statusText").textContent = ok ? "Server online" : "Server error";
  } catch {
    document.querySelector(".status-dot").className = "status-dot err";
    document.getElementById("statusText").textContent = "Server offline";
  }
}
checkServer();
setInterval(checkServer, 10000);

/* ── Weights panel builder ── */
function buildWeightPanels() {
  const el = document.getElementById("weightPanels");
  let html = "";
  CATS.forEach(c => {
    html += `<div class="weight-cat"><div class="weight-cat-header">${c.cat}</div>`;
    c.params.forEach(p => {
      const badgeClass = p.type === "hard" ? "badge-hard" : p.type === "boost" ? "badge-boost" : "badge-std";
      const badgeLabel = p.type === "hard" ? "hard gate" : p.type === "boost" ? "booster" : "standard";
      html += `<div class="weight-row">
        <span class="weight-name">${p.name}</span>
        <span class="badge ${badgeClass}">${badgeLabel}</span>
        <input type="range" min="1" max="20" step="1" value="${weights[p.id]}"
          oninput="weights['${p.id}']=+this.value; this.nextElementSibling.textContent=this.value">
        <span class="weight-val">${weights[p.id]}</span>
      </div>`;
    });
    html += `</div>`;
  });
  el.innerHTML = html;
}
buildWeightPanels();

function resetWeights() {
  weights = { ...DEFAULT_WEIGHTS };
  buildWeightPanels();
}

/* ── Main analysis ── */
async function runAnalysis() {
  const emailText     = document.getElementById("emailText").value.trim();
  const companyDomain = document.getElementById("companyDomain").value.trim();
  const contactDomain = document.getElementById("contactDomain").value.trim();

  if (!emailText && !uploadedFile && !companyDomain && !contactDomain) {
    showError("Please provide at least one input — upload a file, paste email text, or enter domain info.");
    return;
  }

  document.getElementById("errorBox").style.display = "none";
  document.getElementById("reportArea").innerHTML = "";
  document.getElementById("runBtn").disabled = true;

  const streamRow = document.getElementById("streamRow");
  streamRow.style.display = "flex";

  const steps = [
    "Initializing forensic modules…",
    "Analyzing typographic signals…",
    "Running signature forensics…",
    "Checking temporal metadata…",
    "Analyzing visual layout…",
    "Scoring posting authenticity…",
    "LLM semantic enrichment…",
    "Computing formula result…",
  ];
  let stepIdx = 0;
  const ticker = setInterval(() => {
    document.getElementById("streamMsg").textContent = steps[Math.min(stepIdx++, steps.length - 1)];
    document.getElementById("streamSteps").textContent = `${Math.min(stepIdx, steps.length)}/${steps.length}`;
  }, 1500);

  try {
    const formData = new FormData();
    if (uploadedFile) formData.append("file", uploadedFile);
    formData.append("email_text", emailText);
    formData.append("company_domain", companyDomain);
    formData.append("contact_domain", contactDomain);
    formData.append("mode", currentMode);
    formData.append("weights", JSON.stringify(weights));

    const response = await fetch(`${API_BASE}/analyze`, {
      method: "POST",
      body: formData,
    });

    clearInterval(ticker);
    streamRow.style.display = "none";
    document.getElementById("runBtn").disabled = false;

    if (!response.ok) {
      const err = await response.json();
      showError(err.error || `Server error ${response.status}`);
      return;
    }

    const data = await response.json();
    renderReport(data);

  } catch (err) {
    clearInterval(ticker);
    streamRow.style.display = "none";
    document.getElementById("runBtn").disabled = false;
    showError(`Request failed: ${err.message}. Is the Flask server running on port 5000?`);
  }
}

/* ── Report renderer ── */
function renderReport(data) {
  const { scores, formula, verdict_summary, input_type } = data;
  const f  = formula.final_score;
  const vc = f >= 60 ? "high" : f >= 35 ? "medium" : "low";
  const vl = f >= 60 ? "High fraud risk" : f >= 35 ? "Medium fraud risk" : "Low fraud risk";
  const vi = f >= 60 ? "ti-alert-triangle" : f >= 35 ? "ti-alert-circle" : "ti-shield-check";
  const ts = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });

  let html = "";

  /* Verdict card */
  html += `
    <div class="verdict-card ${vc}">
      <div class="score-block">
        <div class="score-num">${f}</div>
        <div class="score-denom">/ 100</div>
      </div>
      <div class="verdict-right">
        <div class="verdict-label"><i class="ti ${vi}"></i> ${vl}</div>
        <div class="verdict-summary">${verdict_summary}</div>
        <div class="formula-trace">${formula.formula}</div>
        ${formula.fired_gates.length ? `<div style="margin-top:6px;font-size:11px;color:var(--danger);font-family:var(--font-mono)">⚡ Hard gates fired: ${formula.fired_gates.join(", ")}</div>` : ""}
      </div>
    </div>`;

  /* Breakdown row */
  const baseColor  = scoreColor(formula.base_score, 100);
  const boostColor = formula.booster_total > 0 ? "var(--danger)" : "var(--success)";
  const finalColor = scoreColor(f, 100);
  html += `
    <div class="breakdown-row">
      <div class="breakdown-card">
        <div class="bd-label">Weighted base</div>
        <div class="bd-val" style="color:${baseColor}">${formula.base_score}<span style="font-size:13px;color:var(--text-muted)">/100</span></div>
      </div>
      <div class="breakdown-card">
        <div class="bd-label">Booster penalties</div>
        <div class="bd-val" style="color:${boostColor}">${formula.booster_total > 0 ? "+" : ""}${formula.booster_total}</div>
      </div>
      <div class="breakdown-card">
        <div class="bd-label">Final fraud score</div>
        <div class="bd-val" style="color:${finalColor}">${f}<span style="font-size:13px;color:var(--text-muted)">/100</span></div>
      </div>
    </div>`;

  /* Radar chart placeholder */
  html += `<div class="radar-wrap"><canvas id="radarCanvas" aria-label="Category-level forensic scores radar chart" role="img"></canvas></div>`;

  /* Timestamp + input type */
  html += `<div style="font-size:11px;font-family:var(--font-mono);color:var(--text-muted);margin-bottom:14px">${ts} IST · input: ${input_type} · mode: ${currentMode}</div>`;

  /* Per-category parameter blocks */
  CATS.forEach(c => {
    const activeParams = c.params.filter(p => {
      const s = scores[p.id];
      return s && s.applicable !== false && s.score !== null;
    });
    const avg = activeParams.length
      ? activeParams.reduce((a, p) => a + scores[p.id].score, 0) / activeParams.length
      : null;

    html += `<div class="cat-block">
      <div class="cat-header">
        <span class="cat-name">${c.cat}</span>
        ${avg !== null
          ? `<span class="cat-avg" style="color:${legScoreColor(avg)}">${avg.toFixed(1)}/10 avg legitimacy</span>`
          : `<span class="na-badge">N/A</span>`}
      </div>`;

    c.params.forEach(p => {
      const s = scores[p.id];
      if (!s) return;
      const ok      = s.applicable !== false && s.score !== null;
      const fired   = ok && ((p.type === "hard" && s.score <= 2) || (p.type === "boost" && s.score <= 3));
      const firedClass = p.type === "hard" ? "fired-hard" : "fired-boost";
      const firedLabel = p.type === "hard" ? "HARD GATE fired" : "BOOSTER fired";
      const badgeClass = p.type === "hard" ? "badge-hard" : p.type === "boost" ? "badge-boost" : "badge-std";
      const badgeLabel = p.type === "hard" ? "hard gate" : p.type === "boost" ? "booster" : "std";

      html += `<div class="param-row">
        <div class="param-top">
          <span class="param-name">${p.name} <span class="badge ${badgeClass}" style="margin-left:4px">${badgeLabel}</span></span>
          <div class="param-right">
            ${fired ? `<span class="fired-badge ${firedClass}">${firedLabel}</span>` : ""}
            ${!ok
              ? `<span class="na-badge">N/A</span>`
              : `<span class="param-score" style="color:${legScoreColor(s.score)}">${s.score}/10</span>`}
            <span class="weight-chip">w=${weights[p.id]}</span>
          </div>
        </div>`;
      if (ok) {
        const pct = (s.score / 10 * 100).toFixed(0);
        html += `<div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${legScoreColor(s.score)}"></div></div>`;
      }
      html += `<div class="param-reason">${s.reason}</div></div>`;
    });

    html += `</div>`;
  });

  /* Formula trace table */
  const traceRows = formula.trace
    .filter(t => t.kind !== "n/a")
    .map(t => `<tr>
      <td>${t.param}</td>
      <td>${t.score !== null && t.score !== undefined ? t.score + "/10" : "—"}</td>
      <td>${t.weight || "—"}</td>
      <td style="color:${t.kind.includes("hard") || t.kind.includes("boost") ? "var(--warning)" : "var(--text-secondary)"}">${t.kind}</td>
      <td>${t.contribution}</td>
    </tr>`).join("");

  html += `<div class="trace-section">
    <div class="trace-title"><i class="ti ti-table"></i> Formula computation trace</div>
    <table class="trace-table">
      <thead><tr><th>Parameter</th><th>Score</th><th>Weight</th><th>Kind</th><th>Contribution</th></tr></thead>
      <tbody>${traceRows}</tbody>
    </table>
    <div class="trace-total">
      Σ weighted fraud contribution / Σ active weights × 100 = base ${formula.base_score}
      &nbsp;+&nbsp; booster penalties = +${formula.booster_total}
      &nbsp;= clamp(${formula.base_score + formula.booster_total}, 0, 100) = <strong>${f}</strong>
    </div>
  </div>`;

  html += `<div class="foot-note"><i class="ti ti-info-circle"></i> &nbsp;N/A parameters indicate the input type doesn't support that analysis (e.g. pixel forensics require PDF/image). Adjust weights in the Weights tab and re-run to recalibrate. HIGH RISK verdict → verify CIN at mca.gov.in and report at cybercrime.gov.in.</div>`;

  document.getElementById("reportArea").innerHTML = html;

  /* Draw radar chart */
  drawRadar(scores, formula);
}

/* ── Radar chart ── */
function drawRadar(scores, formula) {
  const catAvgs = CATS.map(c => {
    const active = c.params.filter(p => {
      const s = scores[p.id];
      return s && s.applicable !== false && s.score !== null;
    });
    return active.length
      ? +(active.reduce((a, p) => a + scores[p.id].score, 0) / active.length).toFixed(1)
      : null;
  });

  const labels = CATS.map(c => c.cat.split(" & ")[0]);
  const validIdx = catAvgs.map((v, i) => v !== null ? i : -1).filter(i => i >= 0);
  const chartLabels = validIdx.map(i => labels[i]);
  const chartData   = validIdx.map(i => catAvgs[i]);

  if (radarChart) { radarChart.destroy(); radarChart = null; }

  const ctx = document.getElementById("radarCanvas");
  if (!ctx) return;

  radarChart = new Chart(ctx, {
    type: "radar",
    data: {
      labels: chartLabels,
      datasets: [{
        label: "Legitimacy score (0–10)",
        data: chartData,
        borderColor: "#3B82F6",
        backgroundColor: "rgba(59,130,246,0.08)",
        borderWidth: 2,
        pointBackgroundColor: "#3B82F6",
        pointRadius: 5,
        pointHoverRadius: 7,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0, max: 10,
          ticks: { stepSize: 2, font: { size: 10, family: "'JetBrains Mono'" }, color: "#536585", backdropColor: "transparent" },
          grid: { color: "rgba(42,58,92,0.8)" },
          pointLabels: { font: { size: 11, family: "'Inter'" }, color: "#8FA3C8" },
          angleLines: { color: "rgba(42,58,92,0.8)" },
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.parsed.r}/10 legitimacy`
          }
        }
      }
    }
  });
}

/* ── Helpers ── */
function showError(msg) {
  const b = document.getElementById("errorBox");
  b.innerHTML = `<i class="ti ti-alert-triangle"></i> ${msg}`;
  b.style.display = "block";
}

function legScoreColor(s) {
  // s = legitimacy score (0-10, higher = more legit)
  if (s >= 7)  return "var(--success)";
  if (s >= 4)  return "var(--warning)";
  return "var(--danger)";
}

function scoreColor(s, max) {
  // s = fraud score (higher = worse)
  const pct = s / max;
  if (pct >= 0.6) return "var(--danger)";
  if (pct >= 0.35) return "var(--warning)";
  return "var(--success)";
}
