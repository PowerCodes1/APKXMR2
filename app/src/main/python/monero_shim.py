"""
Monero Liquid Pro - Python Shim & Android Bridge Wrapper
Complies strictly with the HARD RULE: zero modifications to monero_liquid.py.
All adaptations, caching, and persistence happen in this wrapper.
"""
import sys
import os
import json
import time

_initialized = False
_files_dir = ""
_bridge = None
_network_cache_path = ""
_stats_cache_path = ""
ml = None


def initialize(files_dir: str):
    """
    Sets up the Android environment before importing monero_liquid:
    1. Sets sys.frozen and sys.executable to point to writable app storage.
    2. Imports the fake webview module so import webview succeeds.
    3. Imports monero_liquid as a module without executing its __main__ block.
    4. Restores cached network difficulty so it survives restarts.
    5. Monkeypatches HashVaultEngine to persist future network data.
    """
    global _initialized, _files_dir, _bridge, _network_cache_path, _stats_cache_path, ml
    if _initialized:
        return

    _files_dir = files_dir
    _network_cache_path = os.path.join(files_dir, "network_cache.json")
    _stats_cache_path = os.path.join(files_dir, "stats_cache.json")

    # Step 3: Persistence without touching monero_liquid.py
    # monero_liquid uses: if sys.frozen: BASE_DIR = dirname(sys.executable)
    sys.frozen = True
    sys.executable = os.path.join(files_dir, "app")

    # Ensure fake webview module is loaded
    if "webview" not in sys.modules:
        import webview

    # Import the original file byte-for-byte as a module
    import monero_liquid
    ml = monero_liquid

    # If config.json already contains the fallback wallet, clear it so default is empty
    if os.path.exists(ml.CONFIG_FILE):
        try:
            with open(ml.CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("wallet", "").strip() == ml.FALLBACK_WALLET:
                os.remove(ml.CONFIG_FILE)
                ml.logger.info("[SHIM] Cleared fallback wallet from config.json")
        except Exception:
            pass

    # Clean stats cache if it belongs to fallback wallet
    if os.path.exists(_stats_cache_path):
        try:
            with open(_stats_cache_path, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            if c_data.get("clean_address") == ml.FALLBACK_WALLET:
                os.remove(_stats_cache_path)
                ml.logger.info("[SHIM] Cleared fallback stats cache")
        except Exception:
            pass

    # Custom load_saved_wallet so the initial input is empty until user saves their wallet
    def custom_load_saved_wallet() -> str:
        if os.path.exists(ml.CONFIG_FILE):
            try:
                with open(ml.CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    val = cfg.get("wallet", "").strip()
                    if val and val != ml.FALLBACK_WALLET:
                        return val
            except Exception:
                pass
        return ""

    ml.load_saved_wallet = custom_load_saved_wallet

    # Wrap save_wallet_to_config so bridge's saved_wallet is kept in sync immediately
    orig_save_wallet = ml.save_wallet_to_config

    def wrapped_save_wallet(wallet: str):
        orig_save_wallet(wallet)
        if _bridge:
            _bridge.saved_wallet = wallet.strip()

    ml.save_wallet_to_config = wrapped_save_wallet

    # Step 4: Restore last known network difficulty on startup
    if os.path.exists(_network_cache_path):
        try:
            with open(_network_cache_path, "r", encoding="utf-8") as f:
                cached_net = json.load(f)
                if isinstance(cached_net, dict) and cached_net.get("diff", 0) > 0:
                    cached_net["live"] = False
                    ml.HashVaultEngine._last_network = cached_net
                    ml.logger.info("[SHIM] Restored previous network difficulty from cache")
        except Exception as e:
            ml.logger.warning(f"[SHIM] Error restoring network cache: {e}")

    # Wrap HashVaultEngine.fetch_network to persist successful fetches
    orig_fetch_network = ml.HashVaultEngine.fetch_network

    @classmethod
    def wrapped_fetch_network(cls, pool_data):
        result = orig_fetch_network(pool_data)
        if result and result.get("live"):
            try:
                with open(_network_cache_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception as e:
                ml.logger.warning(f"[SHIM] Error saving network cache: {e}")
        return result

    ml.HashVaultEngine.fetch_network = wrapped_fetch_network

    # Instantiate WebBridge with the saved wallet
    saved_wallet = ml.load_saved_wallet()
    _bridge = ml.WebBridge(saved_wallet)
    _initialized = True
    ml.logger.info("[SHIM] Monero Liquid Python Shim initialized successfully")


RESPONSIVE_CSS = """
<style id="liquid-mobile-responsive">
    /* Global Compact Layout */
    .app-layout {
        max-width: 1320px !important;
        margin: 0 auto !important;
        padding: 12px 14px 28px 14px !important;
        gap: 12px !important;
    }
    .liquid-glass {
        border-radius: 16px !important;
    }
    .liquid-header {
        padding: 12px 18px !important;
        gap: 12px !important;
    }
    .brand-cluster {
        gap: 10px !important;
    }
    .w-logo, .w-logo-fb {
        width: 36px !important;
        height: 36px !important;
    }
    .brand-meta h1 {
        font-size: 16px !important;
    }
    .brand-meta p {
        font-size: 10.5px !important;
    }
    .search-pill-bar {
        gap: 8px !important;
    }
    .input-water-shell {
        padding: 2px 12px !important;
        border-radius: 12px !important;
    }
    .input-water-shell input {
        font-size: 12px !important;
        padding: 8px 0 !important;
    }
    .btn-water {
        padding: 8px 18px !important;
        font-size: 12px !important;
        border-radius: 10px !important;
    }
    .payout-progress-banner {
        padding: 12px 18px !important;
        gap: 6px !important;
    }
    .progress-header, .progress-footer {
        font-size: 12px !important;
    }

    /* Metric Cards: Compact, Balanced & Symmetrical */
    .metric-cards-deck {
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)) !important;
        gap: 10px !important;
    }
    .card-inner-box {
        padding: 12px 14px !important;
        min-height: 104px !important;
        border-radius: 16px !important;
    }
    .card-meta-top {
        font-size: 11px !important;
    }
    .card-number-zone {
        margin: 4px 0 !important;
        gap: 6px !important;
    }
    .card-number-zone .num {
        font-size: 21px !important;
    }
    .card-number-zone .unit {
        font-size: 11.5px !important;
    }
    .card-meta-bot {
        font-size: 10.5px !important;
        padding-top: 6px !important;
    }

    /* Analytics Deck */
    .analytics-deck {
        gap: 12px !important;
    }
    .analytics-deck > div {
        padding: 14px 16px !important;
    }
    .panel-header-wrap {
        margin-bottom: 10px !important;
    }
    .panel-header-wrap h2 {
        font-size: 14px !important;
    }
    .chart-canvas-wrapper {
        height: 220px !important;
    }
    .calc-rows-container {
        gap: 6px !important;
    }
    .calc-liquid-row {
        padding: 8px 12px !important;
        border-radius: 10px !important;
        font-size: 12px !important;
    }
    .diff-box {
        padding: 10px 12px !important;
        margin-top: 4px !important;
    }
    .diff-title {
        font-size: 12px !important;
    }
    .diff-desc {
        font-size: 11px !important;
        line-height: 1.5 !important;
    }

    /* Workers / Miner Telemetry: Tight, Clean & No Excessive Spacing */
    .table-shell {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch;
        margin-top: 4px !important;
    }
    .liquid-table {
        width: 100% !important;
        border-collapse: separate !important;
        border-spacing: 0 4px !important;
        font-size: 12px !important;
    }
    .liquid-table thead tr {
        background: rgba(0, 240, 255, 0.05) !important;
        border-radius: 8px !important;
    }
    .liquid-table th {
        padding: 8px 10px !important;
        font-size: 11px !important;
        font-weight: 700 !important;
        white-space: nowrap !important;
        border-bottom: 1px solid rgba(0, 240, 255, 0.2) !important;
    }
    .liquid-table td {
        padding: 8px 10px !important;
        font-size: 11.5px !important;
        white-space: nowrap !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.04) !important;
    }
    .liquid-table tbody tr {
        background: rgba(2, 8, 20, 0.45) !important;
        border-radius: 8px !important;
    }
    .tag-pill {
        padding: 2px 7px !important;
        font-size: 10px !important;
    }
    .empty-placeholder {
        padding: 18px !important;
        font-size: 12px !important;
    }

    /* Mobile specific refinements */
    @media (max-width: 600px) {
        .app-layout {
            padding: 8px 8px 24px 8px !important;
            gap: 8px !important;
        }
        .liquid-header {
            padding: 10px 12px !important;
        }
        .metric-cards-deck {
            grid-template-columns: repeat(2, 1fr) !important;
            gap: 8px !important;
        }
        .card-inner-box {
            padding: 10px 12px !important;
            min-height: 96px !important;
        }
        .card-number-zone .num {
            font-size: 18px !important;
        }
        .analytics-deck > div {
            padding: 12px !important;
        }
        .chart-canvas-wrapper {
            height: 190px !important;
        }
    }
</style>
"""


def get_html_content() -> str:
    """Generates the HTML string using monero_liquid's own generate_html(), augmented with responsive CSS."""
    global ml
    saved_wallet = ml.load_saved_wallet()
    monero_b64 = ml.get_image_base64("monero")
    coin_b64 = ml.get_image_base64("coin")
    raw_html = ml.generate_html(monero_b64, coin_b64, saved_wallet)
    
    # Inject mobile/compact responsive styles right before </head>
    if "</head>" in raw_html:
        return raw_html.replace("</head>", f"{RESPONSIVE_CSS}\n</head>", 1)
    return raw_html + RESPONSIVE_CSS


def get_initial_wallet() -> str:
    """Returns the currently saved wallet or fallback wallet."""
    global _bridge, ml
    if _bridge:
        return _bridge.get_initial_wallet()
    if ml:
        return ml.load_saved_wallet()
    return ""


def get_cached_stats_json() -> str:
    """Returns the cached stats JSON string if available, or empty string."""
    global _stats_cache_path, ml
    if os.path.exists(_stats_cache_path):
        try:
            with open(_stats_cache_path, "r", encoding="utf-8") as f:
                content = f.read()
            data = json.loads(content)
            saved = ml.load_saved_wallet()
            # Only return cache if the user has a saved wallet matching the cache
            if saved and data.get("clean_address") == saved:
                return content
        except Exception:
            pass
    return ""


def fetch_stats(raw_address: str) -> str:
    """
    Fetches stats via WebBridge.
    Caches successful results. If live request fails, falls back to cache.
    Returns JSON string for Kotlin to dispatch to JavaScript.
    """
    global _bridge, _stats_cache_path, ml
    target_address = (raw_address or "").strip()
    if not target_address:
        saved = ml.load_saved_wallet()
        if saved:
            target_address = saved
        else:
            return json.dumps({"error": "لطفاً ابتدا آدرس والت مونرو را وارد کنید."})

    try:
        res = _bridge.get_stats(target_address)
        if res and not res.get("error"):
            payload = dict(res)
            payload["_cached_at"] = int(time.time())
            try:
                with open(_stats_cache_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except Exception as e:
                ml.logger.warning(f"[SHIM] Error caching stats: {e}")
            return json.dumps(res)
        else:
            cached = get_cached_stats_json()
            if cached:
                ml.logger.info("[SHIM] Request returned error/empty, using cached stats")
                return cached
            return json.dumps(res if res else {"error": "اطلاعاتی دریافت نشد."})
    except Exception as e:
        ml.logger.error(f"[SHIM] Live fetch failed: {e}")
        cached = get_cached_stats_json()
        if cached:
            ml.logger.info("[SHIM] Live fetch exception, using cached stats")
            return cached
        return json.dumps({"error": f"خطا در برقراری ارتباط با استخر: {str(e)}"})
