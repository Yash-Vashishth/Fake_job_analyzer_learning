# Fake Job Offer Forensic Analyzer

A multi-modal forensic framework for detecting fraudulent job offers, with a focus on India-specific fraud patterns. Scores 20 parameters across 5 categories using real signal processing, NLP heuristics, and LLM-assisted semantic analysis — all fused via a custom 3-layer weighted formula.

---

## Quick Start

### 1. Install dependencies
```bash
cd fakejob
pip install -r requirements.txt
```

### 2. Set your API key
```bash
cd backend
cp .env .env.local
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Start the backend
```bash
cd backend
python app.py
# Server starts on http://localhost:5000
```

### 4. Open the frontend
Open `frontend/index.html` in your browser.
(Or serve it: `python -m http.server 8080` from the `frontend/` folder)

---

## Project Structure

```
fakejob/
├── requirements.txt
├── backend/
│   ├── app.py                  Flask API server (entry point)
│   ├── scorer.py               Formula engine
│   ├── llm_assist.py           Anthropic enrichment layer
│   └── forensics/
│       ├── typographic.py      Glyph sharpness, spacing, jitter, renderer
│       ├── signature.py        Ink entropy, Gaussian fit, DCT, texture
│       ├── metadata.py         Causal inversion, timezone, tool anachronism
│       ├── visual_layout.py    Logo lineage, seal, letterhead, color profile
│       └── job_posting.py      Domain edit-dist, urgency NLP, contact, cross-platform
└── frontend/
    ├── index.html
    ├── style.css
    └── app.js
```

---

## API

### `POST /analyze`

**Form fields:**

| Field | Type | Description |
|-------|------|-------------|
| `file` | File | PDF, JPG, PNG, TXT, or EML |
| `email_text` | string | Raw email or posting text |
| `company_domain` | string | Official company domain (e.g. `infosys.com`) |
| `contact_domain` | string | Domain found in the offer (e.g. `infosys-careers.org`) |
| `mode` | string | `research` or `practical` |
| `weights` | JSON string | Custom weight overrides (optional) |

**Response:**
```json
{
  "scores": {
    "domain_legitimacy": { "score": 1, "reason": "...", "applicable": true },
    ...
  },
  "formula": {
    "final_score": 74,
    "base_score": 56,
    "booster_total": 18,
    "verdict": "HIGH_RISK",
    "fired_gates": ["domain_legitimacy"],
    "trace": [...],
    "formula": "WeightedBase=56 + Boosters=18 = clamp(74,0,100) = 74"
  },
  "verdict_summary": "...",
  "input_type": "pdf"
}
```

### `GET /weights`
Returns the default weight configuration.

### `GET /health`
Health check.

---

## Scoring Formula

```
FinalScore = clamp(WeightedBase + BoosterTotal, 0, 100)
```

**Step 1 — Weighted base:**
```
fraud_contribution(p) = (1 − score/10) × weight(p)
WeightedBase = Σ fraud_contribution / Σ active_weights × 100
```

**Step 2 — Hard gates** (causal_inversion, tool_anachronism, domain_legitimacy):
- Fire when score ≤ 2
- Add `weight × 1.2` directly to final score
- Bypass the weighted average entirely

**Step 3 — Mid-tier booster** (urgency_language):
- Fires when score ≤ 3
- Adds `weight × 0.8` AND stays in weighted formula

**Verdict thresholds:**
- ≥ 60 → HIGH RISK
- 35–59 → MEDIUM RISK
- < 35 → LOW RISK

---

## Parameters

### 1. Typographic & Glyph
| ID | Method | Input |
|----|--------|-------|
| `glyph_sharpness` | Laplacian variance on rendered page | PDF / Image |
| `interglyph_spacing` | Char bbox gap variance from rawdict | PDF |
| `baseline_jitter` | Std dev from least-squares fitted baseline | PDF |
| `font_renderer` | Font type count from embedded font table | PDF |

### 2. Signature Forensics
| ID | Method | Input |
|----|--------|-------|
| `ink_spread` | Shannon entropy of ink-region pixel histogram | PDF / Image |
| `edge_gaussian` | Normaltest on Sobel edge magnitude distribution | PDF / Image |
| `dct_misalign` | CV of 8×8 block standard deviations | PDF / Image |
| `bg_texture` | CV of 4×4 region variance grid | PDF / Image |

### 3. Temporal & Metadata
| ID | Method | Input |
|----|--------|-------|
| `causal_inversion` | PDF metadata date ordering checks | PDF |
| `timezone_entropy` | TZ offset mismatch across metadata fields | PDF |
| `tool_anachronism` | Tool release date vs claimed creation date | PDF |

### 4. Visual & Layout
| ID | Method | Input |
|----|--------|-------|
| `logo_compression` | DCT coefficient CV on largest raster embed | PDF / Image |
| `seal_anomaly` | Circularity-based blob detection | PDF / Image |
| `letterhead_deviation` | Pixel density in top 15% of page | PDF / Image |
| `color_profile` | Cross-quadrant RGB mean variance | PDF / Image |

### 5. Job Posting Authenticity
| ID | Method | Input |
|----|--------|-------|
| `domain_legitimacy` | Levenshtein distance + free email check | Text / Any |
| `urgency_language` | Phrase matching + LLM enrichment | Text |
| `contact_verifiability` | Regex: landline, CIN, GST, HR name + LLM | Text |
| `cross_platform` | Portal keyword detection + LLM | Text |

---

## India-Specific Signals

The framework has hardcoded detection for:
- Payment request phrases: "registration fee", "processing fee", "Google Pay", "Paytm", "refundable deposit"
- Free email providers flagged as suspicious for official HR: Gmail, Yahoo, Rediffmail, Hotmail
- CIN format: `[LU]{1}[0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}`
- GST format: `[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9]{1}Z[A-Z0-9]{1}`
- Indian landline format: `0XX-XXXXXXXX`

---

## Notes on N/A Parameters

Parameters are automatically marked N/A when the input type doesn't support analysis:
- Pixel-level parameters (glyph, DCT, ink, texture) require PDF or image
- Metadata parameters (causal inversion, timezone, tool anachronism) require PDF
- Text parameters (urgency, domain, contact) degrade gracefully on any input

N/A parameters are excluded from the weighted formula denominator — they don't drag the score toward 5.
