"""
autodetect.py — System detection and model auto-selection for Conduit

Detects available RAM on macOS (Apple Silicon), subtracts OS + Ableton estimates,
and recommends the appropriate local model tier.
"""

import json
import logging
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("conduit")

# ── Model Tiers ──────────────────────────────────────────────────────

@dataclass
class ModelTier:
    """A recommended model configuration for a given RAM tier."""
    model: str          # Ollama model tag
    quant: str          # Quantization level
    disk_gb: float      # Approximate disk size
    ram_gb: float       # Approximate RAM usage during inference
    min_free_gb: float  # Minimum free RAM required
    max_free_gb: float  # Upper bound (next tier starts here)
    speed_range: str    # Expected tok/s range
    quality: str        # Quality description

# Ordered smallest → largest. Selection picks first tier where free RAM >= min_free_gb.
# llama3.2 only — benchmarked at 100% pass rate for MIDI generation, 10s responses,
# clean output with no thinking-mode leakage. Other models (qwen3, gemma, phi4) are
# either broken (thinking leaks), too slow, or produce musically useless output.
MODEL_TIERS = [
    ModelTier("llama3.2:latest", "Q4_K_M", 2.0, 2.5, 0, 999, "30-50", "Fast, reliable — chat + generate"),
]

# Fallback models if llama3.2 isn't available
FALLBACK_MODELS = [
    "llama3.2",
    "mistral:7b",
]

# Dedicated generate model — llama3.2 only.
# Comprehensive benchmark on Apple M4 16GB: 100% pass rate (with schema bounds +
# validation + retry), 20s avg, correct pitch ranges, velocity dynamics.
GENERATE_MODELS_RANKED = [
    "llama3.2:latest",
    "llama3.2",
]

# Overhead estimates (GB)
OS_OVERHEAD_GB = 3.0
ABLETON_OVERHEAD_GB = 3.0


# ── System Detection ─────────────────────────────────────────────────

def get_total_ram_gb() -> float:
    """Get total system RAM in GB via macOS sysctl."""
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        )
        bytes_total = int(result.stdout.strip())
        return bytes_total / (1024 ** 3)
    except Exception as e:
        logger.warning(f"Could not detect RAM via sysctl: {e}")
        # Fallback: assume 16GB (most common MacBook Air config)
        return 16.0


def get_available_ram_gb() -> float:
    """Estimate available RAM for the LLM after OS + Ableton overhead."""
    total = get_total_ram_gb()
    available = total - OS_OVERHEAD_GB - ABLETON_OVERHEAD_GB
    return max(0, available)


def get_apple_chip_info() -> Optional[str]:
    """Detect Apple Silicon chip model (M1, M2, M3, M4)."""
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return None


# ── Model Selection ──────────────────────────────────────────────────

def recommend_model_tier(available_gb: Optional[float] = None) -> ModelTier:
    """Select the best model tier for the available RAM."""
    if available_gb is None:
        available_gb = get_available_ram_gb()

    # Walk tiers from largest to smallest, pick the biggest that fits
    selected = MODEL_TIERS[0]  # smallest as fallback
    for tier in MODEL_TIERS:
        if available_gb >= tier.min_free_gb:
            selected = tier

    return selected


def get_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """List models currently pulled in Ollama."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def is_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is reachable."""
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def find_best_available_model(
    base_url: str = "http://localhost:11434",
    available_gb: Optional[float] = None,
) -> Optional[str]:
    """
    Find the best model that is both recommended for the system
    AND already pulled in Ollama. Returns None if Ollama isn't
    running or no suitable model is found.
    """
    if not is_ollama_running(base_url):
        return None

    pulled = get_ollama_models(base_url)
    if not pulled:
        return None

    # Normalize pulled model names (strip :latest suffix for comparison)
    pulled_normalized = set()
    for m in pulled:
        pulled_normalized.add(m)
        if ":" not in m:
            pulled_normalized.add(f"{m}:latest")

    recommended = recommend_model_tier(available_gb)

    # Check if the recommended model is pulled
    if recommended.model in pulled_normalized:
        return recommended.model

    # Check all tiers from best to worst that fit our RAM
    for tier in reversed(MODEL_TIERS):
        if available_gb is not None and available_gb < tier.min_free_gb:
            continue
        if tier.model in pulled_normalized:
            return tier.model

    # Check fallback models
    for fallback in FALLBACK_MODELS:
        if fallback in pulled_normalized:
            return fallback

    # Return whatever is pulled (first match)
    return pulled[0] if pulled else None


def find_best_generate_model(
    base_url: str = "http://localhost:11434",
) -> Optional[str]:
    """
    Find the best locally-available model for MIDI JSON generation.
    These models are ranked by their ability to produce valid raw JSON
    reliably, not by general chat quality.
    """
    if not is_ollama_running(base_url):
        return None

    pulled = set(get_ollama_models(base_url))
    for model in GENERATE_MODELS_RANKED:
        if model in pulled:
            return model
    return None


# ── System Report ────────────────────────────────────────────────────

def system_report() -> dict:
    """Generate a full system detection report."""
    total_ram = get_total_ram_gb()
    available = get_available_ram_gb()
    chip = get_apple_chip_info()
    recommended = recommend_model_tier(available)
    ollama_running = is_ollama_running()
    ollama_models = get_ollama_models() if ollama_running else []
    best_available = find_best_available_model(available_gb=available) if ollama_running else None

    return {
        "system": {
            "chip": chip,
            "total_ram_gb": round(total_ram, 1),
            "estimated_available_gb": round(available, 1),
            "os_overhead_gb": OS_OVERHEAD_GB,
            "ableton_overhead_gb": ABLETON_OVERHEAD_GB,
        },
        "recommended_model": {
            "model": recommended.model,
            "quant": recommended.quant,
            "disk_gb": recommended.disk_gb,
            "ram_gb": recommended.ram_gb,
            "speed": recommended.speed_range,
            "quality": recommended.quality,
        },
        "ollama": {
            "running": ollama_running,
            "models_pulled": ollama_models,
            "best_available": best_available,
        },
        "pull_command": f"ollama pull {recommended.model}" if not best_available else None,
    }
