"""
Visual & Layout Forensic Parameters
- Logo compression lineage score
- Seal frequency anomaly score
- Letterhead structural deviation
- Color profile inconsistency
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
            img = Image.open(io.BytesIO(image_data))
            return _analyze_pil_image(img, source="image")
        return _not_applicable("No input provided")
    except Exception as e:
        return _not_applicable(f"Visual analysis error: {str(e)}")


def _analyze_pdf(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    page = doc[0]

    # Extract embedded images
    images = doc.get_page_images(0)
    embedded_imgs = []
    for img_ref in images:
        try:
            xref = img_ref[0]
            base = doc.extract_image(xref)
            pil_img = Image.open(io.BytesIO(base["image"]))
            embedded_imgs.append({
                "pil": pil_img,
                "ext": base["ext"],
                "cs": base.get("colorspace", ""),
                "w": pil_img.width,
                "h": pil_img.height
            })
        except Exception:
            continue

    # Render full page for structural analysis
    pix = page.get_pixmap(dpi=150)
    full_page_img = Image.open(io.BytesIO(pix.tobytes("png")))

    logo_result = _analyze_logo_compression(embedded_imgs)
    seal_result = _analyze_seal(embedded_imgs, full_page_img)
    letterhead_result = _analyze_letterhead(full_page_img, page)
    color_result = _analyze_color_profile(embedded_imgs, full_page_img)

    doc.close()

    return {
        "logo_compression": logo_result,
        "seal_anomaly": seal_result,
        "letterhead_deviation": letterhead_result,
        "color_profile": color_result,
    }


def _analyze_pil_image(img: Image.Image, source: str = "image") -> dict:
    arr = np.array(img.convert("RGB"))
    logo_result = _analyze_logo_compression([])
    seal_result = _analyze_seal([], img)
    letterhead_result = _analyze_letterhead_from_array(arr)
    color_result = _analyze_color_from_array(arr)
    return {
        "logo_compression": logo_result,
        "seal_anomaly": seal_result,
        "letterhead_deviation": letterhead_result,
        "color_profile": color_result,
    }


def _analyze_logo_compression(embedded_imgs: list) -> dict:
    """Detect double-JPEG compression artifacts in raster logo images"""
    jpeg_images = [i for i in embedded_imgs if i["ext"].lower() in ("jpeg", "jpg")]

    if not jpeg_images:
        return {
            "score": 6,
            "reason": "No raster logo images found (likely vector logo or no logo detected); compression lineage check not applicable to vector content",
            "applicable": False
        }

    # Analyze the largest image (most likely logo/header)
    largest = max(jpeg_images, key=lambda i: i["w"] * i["h"])
    arr = np.array(largest["pil"].convert("L")).astype(float)

    h, w = arr.shape
    block_size = 8
    block_dcts = []

    # Compute per-block DCT coefficient variance as proxy for compression generation count
    from scipy.fftpack import dct as scipy_dct
    for r in range(0, h - block_size, block_size):
        for c in range(0, w - block_size, block_size):
            block = arr[r:r+block_size, c:c+block_size]
            d = scipy_dct(scipy_dct(block.T, norm="ortho").T, norm="ortho")
            block_dcts.append(float(np.std(d)))

    if block_dcts:
        dct_cv = float(np.std(block_dcts) / (np.mean(block_dcts) + 1e-9))
        # Low CV = single compression pass; high CV = multiple passes (downloaded & re-saved)
        score = 9 if dct_cv < 0.4 else (6 if dct_cv < 0.7 else (3 if dct_cv < 1.0 else 1))
        reason = (
            f"Logo DCT block coefficient variation={dct_cv:.2f}; "
            f"{'single-generation image — likely from original source files' if score >= 7 else 'double-compression artifacts detected — logo was likely downloaded from web and re-saved, indicating a forged letterhead'}"
        )
    else:
        score = 5
        reason = "Could not compute DCT analysis on embedded image"

    return {"score": score, "reason": reason, "applicable": True}


def _analyze_seal(embedded_imgs: list, full_page: Image.Image) -> dict:
    """Detect presence/absence and authenticity of round seals/stamps"""
    page_arr = np.array(full_page.convert("L"))

    # Look for circular structures using a simplified Hough-like approach
    from scipy.ndimage import label as nd_label

    # Threshold to binary
    binary = (page_arr < 100).astype(np.uint8)
    labeled, num_features = nd_label(binary)

    round_regions = 0
    for region_id in range(1, min(num_features + 1, 200)):
        region = (labeled == region_id)
        area = region.sum()
        if area < 200 or area > 50000:
            continue
        # Check circularity: 4π·area / perimeter²
        from scipy.ndimage import binary_dilation
        perimeter = (binary_dilation(region) ^ region).sum()
        if perimeter == 0:
            continue
        circularity = 4 * np.pi * area / (perimeter ** 2)
        if circularity > 0.5:
            round_regions += 1

    if round_regions >= 1:
        score = 8
        reason = f"Detected {round_regions} circular region(s) consistent with a seal/stamp; presence is a positive authenticity signal"
    elif round_regions == 0:
        score = 4
        reason = "No circular seal/stamp region detected; absence may be legitimate (startups, MNCs) but warrants cross-checking company's known HR practice"
    else:
        score = 5
        reason = "Seal detection inconclusive"

    return {"score": score, "reason": reason, "applicable": True}


def _analyze_letterhead(full_page: Image.Image, page) -> dict:
    """Check structural consistency of letterhead zone (top 15% of page)"""
    arr = np.array(full_page.convert("RGB"))
    h, w = arr.shape[:2]
    header_zone = arr[:int(h * 0.15), :]
    footer_zone = arr[int(h * 0.85):, :]

    # Check pixel density (content presence) in header
    header_content = float(np.mean(header_zone < 200))  # fraction of non-white pixels
    footer_content = float(np.mean(footer_zone < 200))

    # Check color variety in header (logo vs plain text)
    header_rgb = full_page.convert("RGB").crop((0, 0, w, int(h * 0.15)))
    header_colors = len(set(list(header_rgb.getdata())))
    header_colors_norm = min(header_colors / 5000, 1.0)

    # Score heuristic
    if header_content > 0.02 and header_colors_norm > 0.1:
        score = 8
        reason = f"Header zone contains structured content ({header_colors} unique colors, {header_content*100:.1f}% non-white) — letterhead structure is present"
    elif header_content > 0.005:
        score = 5
        reason = "Minimal header content detected; letterhead structure is sparse or plain-text only"
    else:
        score = 3
        reason = "No discernible letterhead structure in top 15% of page — document lacks standard corporate header"

    return {"score": score, "reason": reason, "applicable": True}


def _analyze_letterhead_from_array(arr: np.ndarray) -> dict:
    h, w = arr.shape[:2]
    header = arr[:int(h * 0.15), :]
    content = float(np.mean(header < 200))
    score = 8 if content > 0.02 else (5 if content > 0.005 else 3)
    reason = f"Header content density={content*100:.1f}%; {'letterhead present' if score >= 7 else 'minimal or absent letterhead'}"
    return {"score": score, "reason": reason, "applicable": True}


def _analyze_color_profile(embedded_imgs: list, full_page: Image.Image) -> dict:
    """Detect inconsistent color spaces / profiles across document regions"""
    page_arr = np.array(full_page.convert("RGB")).astype(float)

    # Divide page into quadrants and compare average color statistics
    h, w = page_arr.shape[:2]
    quadrants = [
        page_arr[:h//2, :w//2],
        page_arr[:h//2, w//2:],
        page_arr[h//2:, :w//2],
        page_arr[h//2:, w//2:],
    ]

    means = [q.mean(axis=(0, 1)) for q in quadrants]
    stds = [q.std(axis=(0, 1)) for q in quadrants]

    # Cross-quadrant variance of mean RGB values
    mean_arr = np.array(means)
    cross_var = float(np.mean(np.var(mean_arr, axis=0)))

    # Check for embedded images with different color spaces
    cs_types = set(i["cs"] for i in embedded_imgs if i["cs"])

    if cross_var < 50 and len(cs_types) <= 1:
        score = 8
        reason = f"Color profile consistent across document (cross-quadrant RGB variance={cross_var:.1f}); no colorspace conflicts detected"
    elif cross_var < 150:
        score = 5
        reason = f"Moderate color variance across page regions (cross-quadrant var={cross_var:.1f}); could indicate mixed content types"
    else:
        score = 3
        reason = f"High color inconsistency (cross-quadrant var={cross_var:.1f}); regions likely originate from different source documents or color spaces"

    return {"score": score, "reason": reason, "applicable": True}


def _analyze_color_from_array(arr: np.ndarray) -> dict:
    farr = arr.astype(float)
    h, w = farr.shape[:2]
    q = [farr[:h//2, :w//2], farr[:h//2, w//2:], farr[h//2:, :w//2], farr[h//2:, w//2:]]
    means = np.array([qi.mean(axis=(0,1)) for qi in q])
    cv = float(np.mean(np.var(means, axis=0)))
    score = 8 if cv < 50 else (5 if cv < 150 else 3)
    reason = f"Color variance across regions={cv:.1f}; {'consistent color profile' if score >= 7 else 'color inconsistency detected'}"
    return {"score": score, "reason": reason, "applicable": True}


def _not_applicable(reason: str) -> dict:
    keys = ["logo_compression", "seal_anomaly", "letterhead_deviation", "color_profile"]
    return {k: {"score": None, "reason": reason, "applicable": False} for k in keys}
