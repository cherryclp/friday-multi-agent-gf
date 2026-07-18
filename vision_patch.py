# ============================================================
#  JARVIS VISION PATCH
# ============================================================

import base64
import io
import time
import pyautogui
import requests
from PIL import Image

# ── CONFIG ──────────────────────────────────────────────────
LMS_BASE          = "http://localhost:1234/v1"
VISION_MODEL      = "llava-1.6-mistral-7b"
SCREENSHOT_SCALE  = 0.35  # smaller = faster LLaVA response, still readable
VISION_MAX_TOKENS = 200
# ────────────────────────────────────────────────────────────

SYSTEM_MSG = (
    "You are a screen reader for an AI assistant called Jarvis. "
    "Describe ONLY what is clearly and literally visible on screen. "
    "List app names, window titles, and only text you can clearly read. "
    "IMPORTANT: If you see a chat interface with the name JARVIS, that is the AI assistant "
    "talking to the user — not a human. Do not describe it as the user chatting with someone. "
    "Do NOT guess, invent, or hallucinate anything. Be brief and factual. Max 3 sentences."
)


def _take_screenshot() -> Image.Image:
    """Take screenshot — tries mss first (faster + more reliable), falls back to pyautogui."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # full virtual screen
            raw = sct.grab(monitor)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except ImportError:
        pass  # mss not installed, fall back
    except Exception as e:
        print(f"[VISION] mss failed: {e}, falling back to pyautogui")
    return pyautogui.screenshot()


def _compress_screenshot(img: Image.Image, scale: float = SCREENSHOT_SCALE) -> str:
    new_w = int(img.width  * scale)
    new_h = int(img.height * scale)
    img   = img.resize((new_w, new_h), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=50)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def _ask_vision(b64_image: str, prompt: str) -> str:
    if not VISION_MODEL:
        return "ERROR: VISION_MODEL is empty in vision_patch.py!! set it to your LM Studio vision model name 😭"

    url = f"{LMS_BASE}/chat/completions"

    # Format A: image as separate top-level field (some LM Studio builds)
    payload_a = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": prompt, "images": [b64_image]}
        ]
    }

    # Format B: image embedded as data URI in a text block (llava-cpp style)
    payload_b = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": f"![image](data:image/jpeg;base64,{b64_image})\n\n{prompt}"}
        ]
    }

    # Format C: OpenAI vision spec — image_url inside content array
    payload_c = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text",      "text": f"{SYSTEM_MSG}\n\n{prompt}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
                ]
            }
        ]
    }

    for label, payload in [("A", payload_a), ("B", payload_b), ("C", payload_c)]:
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 400:
                print(f"[VISION] format {label} failed (400), trying next...")
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"[VISION] format {label} worked!!")
            return content.strip()
        except requests.exceptions.ConnectionError:
            return "ERROR: can't reach LM Studio babe — is it running?? 😭"
        except requests.exceptions.Timeout:
            return "ERROR: vision timed out 😭 try lowering SCREENSHOT_SCALE"
        except Exception as e:
            print(f"[VISION] format {label} exception: {e}")
            continue

    return "ERROR: all vision formats failed 😭 check LM Studio logs for clues babe"


def look_at_screen(prompt: str = "what do u see on my screen?") -> str:
    try:
        time.sleep(1.5)
        img = _take_screenshot()
        b64 = _compress_screenshot(img)
        return _ask_vision(b64, prompt)
    except Exception as e:
        return f"ERROR: screenshot failed: {str(e)}"


def look_at_screen_region(region: str = "full", prompt: str = "what's on my screen?") -> str:
    try:
        time.sleep(1.5)
        img = _take_screenshot()
        w, h = img.width, img.height
        crop_map = {
            "full":   (0,     0,     w,      h     ),
            "left":   (0,     0,     w//2,   h     ),
            "right":  (w//2,  0,     w,      h     ),
            "top":    (0,     0,     w,      h//2  ),
            "bottom": (0,     h//2,  w,      h     ),
            "center": (w//4,  h//4,  3*w//4, 3*h//4),
        }
        box = crop_map.get(region.lower(), crop_map["full"])
        img = img.crop(box)
        # FIX B19: region screenshots use higher scale than full (already cropped, so still smaller total pixels)
        b64 = _compress_screenshot(img, scale=0.6)
        return _ask_vision(b64, prompt)
    except Exception as e:
        return f"ERROR: region screenshot failed: {str(e)}"