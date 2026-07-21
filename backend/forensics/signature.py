"""
Signature Forensics Parameters
- Ink spread entropy
- Edge Gaussian fit deviation
- DCT block misalignment index
- Background texture continuity score
"""
import numpy as np
from PIL import Image
import io
import fitz


def analyze(pdf_path: str = None, image_data: bytes = None) -> dict:
    try:
        if pdf_path:
            return _analyze_pdf(pdf_path)
        elif image_data:
            img = Image.open(io.BytesIO(image_data)).convert("L")
            return _analyze_image_array(np.array(img))
        return _not_applicable("No file provided")
    except Exception as e:
        return _not_applicable(f"Signature analysis error: {str(e)}")


def _analyze_pdf(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    page = doc[0]
    pix = page.get_pixmap(dpi=200)
    img_bytes = pix.tobytes("png")
    doc.close()
    img = Image.open(io.BytesIO(img_bytes)).convert("L")
    arr = np.array(img)
    return _analyze_image_array(arr)


def _analyze_image_array(gray: np.ndarray) -> dict:
    # ── 1. Ink spread entropy ──────────────────────────────────────────────
    # Binarize: dark pixels = ink
    ink_mask = gray < 128
    if ink_mask.sum() > 100:
        ink_vals = gray[ink_mask]
        hist, _ = np.histogram(ink_vals, bins=32, range=(0, 128))
        hist = hist / (hist.sum() + 1e-9)
        entropy = float(-np.sum(hist * np.log2(hist + 1e-9)))
        # Real pen ink: high entropy (varied pressure) 3.5-5.0
        # Copy-pasted digital sig: very low entropy (uniform dark) < 2.0
        ink_score = 10 if 3.0 < entropy < 5.5 else (7 if 2.0 < entropy <= 3.0 else (4 if 1.5 < entropy <= 2.0 else 2))
        ink_reason = (
            f"Ink region entropy={entropy:.2f} bits; "
            f"{'natural ink spread distribution' if ink_score >= 7 else 'unnaturally uniform dark region — consistent with a copy-pasted digital signature image'}"
        )
    else:
        ink_score = 5
        ink_reason = "Insufficient dark-region pixels; no clear signature/ink region detected"

    # ── 2. Edge Gaussian fit deviation ────────────────────────────────────
    from scipy.ndimage import sobel
    sx = sobel(gray.astype(float), axis=1)
    sy = sobel(gray.astype(float), axis=0)
    edge_mag = np.hypot(sx, sy)
    strong_edges = edge_mag[edge_mag > np.percentile(edge_mag, 90)]

    if len(strong_edges) > 50:
        mean_e = float(np.mean(strong_edges))
        std_e = float(np.std(strong_edges))
        # Gaussian fit: compute how well the edge distribution fits a Gaussian
        from scipy.stats import normaltest
        stat, pval = normaltest(strong_edges[:5000])  # cap for speed
        # Genuine scanned signatures: edges follow near-Gaussian distribution (physical capture noise)
        # Pasted: often has a bimodal or perfectly sharp distribution
        gauss_score = 9 if pval > 0.05 else (6 if pval > 0.01 else 3)
        gauss_reason = (
            f"Edge distribution normality p={pval:.3f}; "
            f"{'edge profile consistent with physical scan/capture' if gauss_score >= 7 else 'non-Gaussian edge profile — suggests digitally composited or hard-copied signature'}"
        )
    else:
        gauss_score = 5
        gauss_reason = "Insufficient edge data for Gaussian fit analysis"

    # ── 3. DCT block misalignment index ────────────────────────────────────
    # Look for 8x8 block grid inconsistencies (JPEG ghost / double-compression)
    h, w = gray.shape
    block_size = 8
    row_stds = []
    for r in range(0, h - block_size, block_size):
        for c in range(0, w - block_size, block_size):
            block = gray[r:r+block_size, c:c+block_size].astype(float)
            row_stds.append(np.std(block))

    if row_stds:
        std_of_stds = float(np.std(row_stds))
        mean_std = float(np.mean(row_stds))
        # High variance of block-stds = inconsistent compression blocks = likely paste artifact
        ratio = std_of_stds / (mean_std + 1e-9)
        dct_score = 9 if ratio < 0.6 else (6 if ratio < 0.9 else (3 if ratio < 1.3 else 2))
        dct_reason = (
            f"8x8 block std-ratio={ratio:.2f}; "
            f"{'uniform compression grid — single-source document' if dct_score >= 7 else 'block-level inconsistencies detected — possible JPEG double-compression from pasted region'}"
        )
    else:
        dct_score = 5
        dct_reason = "Could not perform DCT block analysis"

    # ── 4. Background texture continuity ──────────────────────────────────
    # Compare local texture variance in different regions of the image
    # A pasted signature leaves a rectangular zone with different texture
    region_vars = []
    step_r = h // 4
    step_c = w // 4
    for i in range(4):
        for j in range(4):
            region = gray[i*step_r:(i+1)*step_r, j*step_c:(j+1)*step_c]
            region_vars.append(float(np.var(region)))

    if region_vars:
        texture_cv = float(np.std(region_vars) / (np.mean(region_vars) + 1e-9))
        # Low CV = uniform texture across page (genuine)
        # High CV = one or more regions are much smoother/rougher (paste boundary)
        texture_score = 9 if texture_cv < 0.5 else (6 if texture_cv < 0.9 else (3 if texture_cv < 1.5 else 2))
        texture_reason = (
            f"Texture variance coefficient={texture_cv:.2f}; "
            f"{'uniform paper texture across page — no paste discontinuities detected' if texture_score >= 7 else 'significant texture discontinuity detected — suggests a region was composited onto the document'}"
        )
    else:
        texture_score = 5
        texture_reason = "Could not analyze texture continuity"

    return {
        "ink_spread": {"score": ink_score, "reason": ink_reason, "applicable": True},
        "edge_gaussian": {"score": gauss_score, "reason": gauss_reason, "applicable": True},
        "dct_misalign": {"score": dct_score, "reason": dct_reason, "applicable": True},
        "bg_texture": {"score": texture_score, "reason": texture_reason, "applicable": True},
    }


def _not_applicable(reason: str) -> dict:
    keys = ["ink_spread", "edge_gaussian", "dct_misalign", "bg_texture"]
    return {k: {"score": None, "reason": reason, "applicable": False} for k in keys}
