"""
Typographic & Glyph-Level Forensic Parameters
- Glyph edge sharpness score
- Inter-glyph spacing variance
- Baseline jitter index
- Font renderer consistency score
"""
import numpy as np
from PIL import Image
import io
import fitz  # PyMuPDF


def analyze(pdf_path: str = None, image_data: bytes = None) -> dict:
    results = {}

    if pdf_path:
        results.update(_analyze_pdf(pdf_path))
    elif image_data:
        img = Image.open(io.BytesIO(image_data)).convert("L")
        results.update(_analyze_image(np.array(img)))

    return results


def _analyze_pdf(pdf_path: str) -> dict:
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]

        # --- Font renderer consistency ---
        # Check if multiple font renderers are present (different font subsets/types)
        fonts = doc.get_page_fonts(0)
        font_types = set()
        font_names = []
        for f in fonts:
            font_types.add(f[2])  # font type: Type1, TrueType, CIDFont, etc.
            font_names.append(f[3])

        # More than 2 distinct renderer types suggests compositing from different sources
        renderer_score = 10 if len(font_types) <= 1 else (7 if len(font_types) == 2 else 3)

        # --- Inter-glyph spacing variance via text blocks ---
        blocks = page.get_text("rawdict")["blocks"]
        all_char_gaps = []
        baseline_ys = []

        for b in blocks:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                spans = line.get("spans", [])
                baseline_ys.append(line["bbox"][3])  # bottom y of line = baseline proxy
                for span in spans:
                    chars = span.get("chars", [])
                    for i in range(1, len(chars)):
                        prev_end = chars[i - 1]["bbox"][2]
                        curr_start = chars[i]["bbox"][0]
                        gap = curr_start - prev_end
                        if -5 < gap < 30:  # filter outliers
                            all_char_gaps.append(gap)

        if len(all_char_gaps) > 10:
            gap_variance = float(np.var(all_char_gaps))
            # Legitimate docs: variance < 2.0 (algorithmic kerning)
            # Edited/composed: variance > 5.0
            spacing_score = 10 if gap_variance < 1.5 else (7 if gap_variance < 3.0 else (4 if gap_variance < 6.0 else 1))
            spacing_reason = f"Char gap variance={gap_variance:.2f}px; {'consistent algorithmic kerning' if gap_variance < 3 else 'irregular spacing suggests manual composition'}"
        else:
            spacing_score = 5
            spacing_reason = "Insufficient character data for spacing analysis"

        # --- Baseline jitter ---
        if len(baseline_ys) > 3:
            # Compute deviations from a fitted line (least-squares)
            x = np.arange(len(baseline_ys))
            coeffs = np.polyfit(x, baseline_ys, 1)
            fitted = np.polyval(coeffs, x)
            jitter = float(np.std(np.array(baseline_ys) - fitted))
            # Low jitter (<1.5px) = programmatic layout; high (>4px) = pasted/rasterized text
            jitter_score = 10 if jitter < 1.0 else (7 if jitter < 2.0 else (4 if jitter < 4.0 else 2))
            jitter_reason = f"Baseline jitter std={jitter:.2f}px; {'text is programmatically laid out' if jitter < 2 else 'irregular baseline suggests text was pasted or image-converted'}"
        else:
            jitter_score = 5
            jitter_reason = "Insufficient line data for baseline jitter analysis"

        renderer_reason = (
            f"Font types detected: {list(font_types)}; "
            f"{'single renderer — consistent document origin' if len(font_types) <= 1 else 'multiple renderer fingerprints suggest document was composited from different sources'}"
        )

        # Glyph sharpness needs rasterization — render page to image
        pix = page.get_pixmap(dpi=150)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img_array = img_array[:, :, :3]
        gray = np.mean(img_array, axis=2).astype(np.uint8)
        sharpness_data = _sharpness_from_array(gray)

        doc.close()

        return {
            "glyph_sharpness": {"score": sharpness_data["score"], "reason": sharpness_data["reason"], "applicable": True},
            "interglyph_spacing": {"score": spacing_score, "reason": spacing_reason, "applicable": True},
            "baseline_jitter": {"score": jitter_score, "reason": jitter_reason, "applicable": True},
            "font_renderer": {"score": renderer_score, "reason": renderer_reason, "applicable": True},
        }

    except Exception as e:
        return _not_applicable(f"PDF typographic analysis failed: {str(e)}")


def _analyze_image(gray: np.ndarray) -> dict:
    sharpness = _sharpness_from_array(gray)
    return {
        "glyph_sharpness": {"score": sharpness["score"], "reason": sharpness["reason"], "applicable": True},
        "interglyph_spacing": {"score": 5, "reason": "Cannot measure char-level spacing from raster image without OCR", "applicable": False},
        "baseline_jitter": {"score": 5, "reason": "Baseline jitter requires vector text; using image scan", "applicable": False},
        "font_renderer": {"score": 5, "reason": "Font renderer fingerprinting requires PDF vector data", "applicable": False},
    }


def _sharpness_from_array(gray: np.ndarray) -> dict:
    from scipy.ndimage import laplace
    lap = laplace(gray.astype(float))
    sharpness = float(np.var(lap))
    # High variance = sharp edges (genuine vector text); low = blurry (rasterized/screenshotted)
    score = 10 if sharpness > 800 else (8 if sharpness > 400 else (5 if sharpness > 150 else 2))
    reason = (
        f"Laplacian variance={sharpness:.1f}; "
        f"{'crisp vector-rendered text edges' if sharpness > 400 else 'soft/blurry text edges suggest rasterization, screenshot, or scan-and-repaste'}"
    )
    return {"score": score, "reason": reason}


def _not_applicable(reason: str) -> dict:
    keys = ["glyph_sharpness", "interglyph_spacing", "baseline_jitter", "font_renderer"]
    return {k: {"score": None, "reason": reason, "applicable": False} for k in keys}
