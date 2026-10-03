import os
import sys
import re
import time
import json
import base64
import logging
import tempfile
from datetime import datetime
import requests
import webview

# ==================== تشخیص مسیر فایل (پایتون یا EXE مستقل) ====================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "telemetry_history.json")
FALLBACK_WALLET = "87DFWGpThiGYegNCbmDUYBHWYuRXRd6xLbvork7S8B9E2rtbqR1vvJC3HcdCZ8cRhXLJUuN7eiYwpVyw68JKF9DcQVkpK4p"

# ==================== لاگ‌های رنگی ترمینال پایتون ====================
class ColoredTerminalFormatter(logging.Formatter):
    CYAN, GREEN, YELLOW, RED, RESET = "\033[96m", "\033[92m", "\033[93m", "\033[91m", "\033[0m"
    FORMATS = {
        logging.DEBUG: f"{CYAN}[DEBUG]{RESET} %(asctime)s - %(message)s",
        logging.INFO: f"{GREEN}[INFO]{RESET} %(asctime)s - %(message)s",
        logging.WARNING: f"{YELLOW}[WARNING]{RESET} %(asctime)s - %(message)s",
        logging.ERROR: f"{RED}[ERROR]{RESET} %(asctime)s - %(message)s",
    }
    def format(self, record):
        fmt = self.FORMATS.get(record.levelno, "%(asctime)s - %(levelname)s - %(message)s")
        return logging.Formatter(fmt, datefmt="%H:%M:%S").format(record)

logger = logging.getLogger("LiquidMonitor")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredTerminalFormatter())
logger.addHandler(handler)

# ==================== مدیریت کانفیگ و دیتابیس محلی JSON ====================
def load_saved_wallet() -> str:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                val = cfg.get("wallet", "").strip()
                if val: return val
        except Exception as e:
            logger.error(f"خطا در خواندن config.json: {e}")
    return FALLBACK_WALLET

def save_wallet_to_config(wallet: str):
    if not wallet or len(wallet.strip()) < 90: return
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"wallet": wallet.strip(), "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
        logger.info(f"[CONFIG] والت به صورت دائمی ذخیره شد: {wallet[:16]}...")
    except Exception as e:
        logger.error(f"خطا در ذخیره config.json: {e}")

def load_local_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list): return data
        except Exception as e:
            logger.error(f"خطا در خواندن telemetry_history.json: {e}")
    return []

def save_local_snapshot(timestamp, hashrate, balance, paid):
    history = load_local_history()
    if history and abs(timestamp - history[-1].get("timestamp", 0)) < 25:
        return history

    history.append({
        "timestamp": timestamp,
        "hashrate": round(hashrate, 1),
        "balance": round(balance, 6),
        "paid": round(paid, 6)
    })
    if len(history) > 3000:
        history = history[-3000:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
        logger.info(f"[DATABASE] نقطه داده جدید ثبت شد: {hashrate} H/s | تعداد کل رکوردها: {len(history)}")
    except Exception as e:
        logger.error(f"خطا در نوشتن تاریخچه محلی: {e}")
    return history

def seed_history_from_pool(chart_raw):
    history = load_local_history()
    if len(history) > 5 or not chart_raw: return history

    new_points = []
    if isinstance(chart_raw, list):
        for p in chart_raw:
            if isinstance(p, dict):
                ts = int(to_float(p.get("timestamp") or p.get("time")))
                hr = to_float(p.get("hashRate") or p.get("hashrate") or p.get("hs"))
                if ts > 0: new_points.append({"timestamp": ts, "hashrate": round(hr, 1), "balance": 0.0, "paid": 0.0})
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                ts = int(to_float(p[0]))
                hr = to_float(p[1])
                if ts > 0: new_points.append({"timestamp": ts, "hashrate": round(hr, 1), "balance": 0.0, "paid": 0.0})

    if new_points:
        new_points.sort(key=lambda x: x["timestamp"])
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(new_points, f, indent=2)
            logger.info(f"[DATABASE] تاریخچه اولیه ۲۴ ساعته با موفقیت ذخیره شد ({len(new_points)} نقطه).")
            return new_points
        except Exception: pass
    return history

# ==================== توابع تبدیل امن داده‌ها ====================
def to_float(val, default=0.0) -> float:
    if val is None: return default
    if isinstance(val, (int, float)): return float(val)
    if isinstance(val, str):
        try: return float(val.strip())
        except: return default
    if isinstance(val, dict):
        for k in ("balance", "amount", "value", "hashRate", "hashrate", "confirmed"):
            if k in val: return to_float(val[k], default)
    return default

def piconero_to_xmr(val) -> float:
    num = to_float(val)
    return num / (10**12) if num > 1_000_000 else num

def find_key(obj, key):
    """جستجوی بازگشتی یک کلید در JSON تو در تو"""
    if isinstance(obj, dict):
        if obj.get(key) not in (None, "", 0):
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None: return r
    return None

def humanize_duration_fa(seconds: float) -> str:
    if seconds <= 0: return "--"
    if seconds < 3600: return f"{int(seconds // 60)} دقیقه"
    if seconds < 86400: return f"{seconds / 3600:.1f} ساعت"
    if seconds < 86400 * 365: return f"{seconds / 86400:.1f} روز"
    return f"{seconds / (86400 * 365):.1f} سال"

def get_image_base64(name):
    for p in [os.path.join(BASE_DIR, f"{name}.png"), os.path.join(BASE_DIR, f"{name}.jpg"), f"{name}.png"]:
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    return f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
            except Exception: pass
    return None

# ==================== موتور دریافت و محاسبات HashVault v3 ====================
class HashVaultEngine:
    SERVERS = ["https://api.hashvault.pro/v3/monero", "https://api.hashvault.sh/v3/monero"]
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
    NET_FALLBACKS = ["https://xmrchain.net/api/networkinfo", "https://moneroblocks.info/api/get_stats"]
    _last_network = {}   # آخرین اطلاعات موفق شبکه

    @staticmethod
    def extract_wallet(raw: str) -> str:
        s = raw.strip()
        m = re.search(r'[48][0-9a-zA-Z]{94,105}', s)
        if m: return m.group(0)
        if "hashvault.pro" in s:
            parts = [p for p in s.split("/") if p and not p.startswith("http") and p not in ("dashboard", "monero")]
            if parts: return parts[-1].split("?")[0].strip()
        return s

    @classmethod
    def request_json(cls, endpoint: str):
        for base in cls.SERVERS:
            url = f"{base}{endpoint}"
            t0 = time.time()
            try:
                res = requests.get(url, headers=cls.HEADERS, timeout=10)
                logger.info(f"پاسخ از {base}: وضعیت {res.status_code} ({int((time.time() - t0)*1000)}ms)")
                if res.status_code == 200: return res.json()
            except Exception as e:
                logger.warning(f"تایم‌اوت {base}: {e}")
        return None

    # ---------- ورکرها (endpoint جداگانه) ----------
    @staticmethod
    def _norm_worker(w, name=None):
        if not isinstance(w, dict):
            w = {"hashRate": w}
        hr = to_float(w.get("hashRate") or w.get("hashrate") or w.get("hs"))
        ls = to_float(w.get("lastShare") or w.get("lastHash"))
        if ls > 1e11: ls /= 1000.0          # میلی‌ثانیه -> ثانیه
        mean = to_float(w.get("avg24hashRate") or w.get("meanHashRate") or w.get("meanHashrate"))
        return {
            "name": str(name or w.get("name") or w.get("worker") or w.get("workerName") or "Rig"),
            "hashrate": f"{hr:.1f} H/s",
            "mean": f"{mean:.1f} H/s",
            "last_share": int(ls),
            "online": hr > 0 and not w.get("offline", False),
        }

    @classmethod
    def fetch_workers(cls, wallet, stats_workers_fallback):
        data = cls.request_json(f"/wallet/{wallet}/workers")
        found = []
        if isinstance(data, dict):
            for grp in ("collective", "solo"):
                items = data.get(grp)
                if isinstance(items, list):
                    found += [cls._norm_worker(w) for w in items]
                elif isinstance(items, dict):
                    found += [cls._norm_worker(w, name=k) for k, w in items.items()]
            if not found and isinstance(data.get("workers"), list):
                found = [cls._norm_worker(w) for w in data["workers"]]
        elif isinstance(data, list):
            found = [cls._norm_worker(w) for w in data]

        if not found and stats_workers_fallback:   # مسیر پشتیبان قدیمی
            if isinstance(stats_workers_fallback, list):
                found = [cls._norm_worker(w) for w in stats_workers_fallback]
            elif isinstance(stats_workers_fallback, dict):
                found = [cls._norm_worker(w, name=k) for k, w in stats_workers_fallback.items()]

        if found:
            logger.debug(f"[WORKERS] {len(found)} ورکر دریافت شد: {[w['name'] for w in found]}")
        return [w for w in found if w["online"]]

    # ---------- اطلاعات زنده شبکه ----------
    @classmethod
    def fetch_network(cls, pool_data):
        """سختی/ارتفاع/پاداش واقعی شبکه؛ بدون عدد ساختگی"""
        net = pool_data.get("network") if isinstance(pool_data.get("network"), dict) else {}
        diff = to_float(net.get("difficulty"))
        height = int(to_float(net.get("height")))
        reward = piconero_to_xmr(net.get("reward") or 0)
        source = "HashVault"

        if diff <= 0:
            for url in cls.NET_FALLBACKS:
                try:
                    r = requests.get(url, headers=cls.HEADERS, timeout=8)
                    if r.status_code != 200: continue
                    j = r.json()
                    diff = to_float(find_key(j, "difficulty"))
                    height = int(to_float(find_key(j, "height")))
                    rw = find_key(j, "last_reward") or find_key(j, "reward")
                    reward = piconero_to_xmr(rw) if rw else 0.0
                    if diff > 0:
                        source = url.split("/")[2]
                        break
                except Exception as e:
                    logger.warning(f"خطا در دریافت سختی از {url}: {e}")

        if diff > 0:
            info = {"diff": diff, "height": height, "reward": reward or 0.6, "source": source, "live": True}
            cls._last_network = info
            return info
        if cls._last_network:
            return {**cls._last_network, "live": False}
        return None

    @classmethod
    def fetch_telemetry(cls, raw_address: str):
        wallet = cls.extract_wallet(raw_address)
        if len(wallet) < 90:
            return {"error": "آدرس وارد شده یک والت معتبر مونرو نیست."}

        save_wallet_to_config(wallet)
        pool_data = cls.request_json("") or {}
        wallet_data = cls.request_json(f"/wallet/{wallet}/stats?workers=true&chart=true&period=daily&poolType=false&inactivityThreshold=10080")

        if not wallet_data:
            return {"error": "اطلاعاتی در استخر ثبت نشده یا هنوز شیری ارسال نکرده‌اید."}

        collective = wallet_data.get("collective", {}) if isinstance(wallet_data.get("collective"), dict) else {}
        revenue = wallet_data.get("revenue", {}) if isinstance(wallet_data.get("revenue"), dict) else {}
        pool_stats = pool_data.get("pool", {}) if isinstance(pool_data.get("pool"), dict) else {}

        current_hashrate = to_float(collective.get("hashRate") or wallet_data.get("hashRate"))
        mean_hashrate = to_float(collective.get("meanHashRate") or wallet_data.get("meanHashRate"))
        total_hashes = int(to_float(collective.get("totalHashes") or wallet_data.get("totalHashes")))
        last_share = int(to_float(collective.get("lastShare") or wallet_data.get("lastShare")))

        balance_xmr = piconero_to_xmr(revenue.get("confirmedBalance") or wallet_data.get("confirmedBalance") or wallet_data.get("balance"))
        unconfirmed_xmr = piconero_to_xmr(revenue.get("unconfirmedBalance") or wallet_data.get("unconfirmedBalance"))
        paid_xmr = piconero_to_xmr(revenue.get("paid") or wallet_data.get("paid"))

        seed_history_from_pool(wallet_data.get("chart") or collective.get("chart"))
        local_history = save_local_snapshot(int(time.time()), current_hashrate, balance_xmr, paid_xmr)

        # ---------- ورکرها ----------
        workers_list = cls.fetch_workers(wallet, wallet_data.get("workers") or collective.get("workers"))

        if len(workers_list) == 0 and current_hashrate > 0:
            workers_list.append({
                "name": "Unknown Rig",
                "hashrate": f"{current_hashrate:.1f} H/s",
                "mean": f"{mean_hashrate:.1f} H/s",
                "last_share": last_share,
                "online": True
            })

        # ---------- شبکه و سودآوری ----------
        net = cls.fetch_network(pool_data)
        pool_fee = to_float(pool_stats.get("fee", 0.9)) / 100.0

        daily_xmr = 0.0
        net_hashrate = 0.0
        diff_billion = 0.0
        if net:
            net_diff = net["diff"]
            diff_billion = net_diff / 1e9
            net_hashrate = net_diff / 120.0        # زمان هر بلاک مونرو ≈ ۱۲۰ ثانیه
            if current_hashrate > 0:
                daily_xmr = (current_hashrate / net_hashrate) * 720.0 * net["reward"] * (1.0 - pool_fee)

        hourly_xmr = daily_xmr / 24.0
        weekly_xmr = daily_xmr * 7.0
        monthly_xmr = daily_xmr * 30.0

        TARGET_PAYOUT = 0.001
        progress_pct = min(100.0, (balance_xmr / TARGET_PAYOUT) * 100.0)
        remaining = max(0.0, TARGET_PAYOUT - balance_xmr)

        if remaining == 0:
            payout_eta = "آماده تسویه خودکار استخر! (۱۰۰٪ تکمیل شد)"
        elif daily_xmr > 0:
            h_left = remaining / hourly_xmr
            payout_eta = f"{int(h_left)} ساعت دیگر تا 0.001 XMR" if h_left < 24 else f"{h_left / 24:.1f} روز دیگر تا 0.001 XMR"
        else:
            payout_eta = "نیازمند ماینر آنلاین برای تخمین زمان"

        est_share_sec = int(50000 / current_hashrate) if current_hashrate > 0 else 0

        if net:
            if net["live"]:
                diff_status = f"داده زنده از {net['source']} • بلوک {net['height']:,}"
            else:
                diff_status = "اتصال برقرار نشد • آخرین مقدار ذخیره‌شده"
            diff_explanation = (
                f"سختی شبکه یعنی پیدا کردن هر بلوک چقدر سخت است؛ هرچه بالاتر، رقابت بیشتر. "
                f"الان کل شبکه مونرو حدود {net_hashrate / 1e9:.2f} گیگاهش بر ثانیه قدرت دارد. "
            )
            if current_hashrate > 0:
                solo_sec = (net_hashrate / current_hashrate) * 120.0
                diff_explanation += (
                    f"با قدرت فعلی شما ({current_hashrate:,.0f} H/s)، اگر تنها ماین می‌کردید "
                    f"به‌طور میانگین هر {humanize_duration_fa(solo_sec)} یک بلوک پیدا می‌کردید. "
                    f"استخر این کار را بین همه ماینرها تقسیم می‌کند و سهم شما را به نسبت قدرتتان می‌پردازد."
                )
            else:
                diff_explanation += "ماینری آنلاین نیست، پس تخمین سود قابل محاسبه نیست."
        else:
            diff_status = "اطلاعات شبکه در دسترس نیست"
            diff_explanation = "دریافت سختی زنده شبکه ممکن نشد؛ اتصال اینترنت را بررسی کنید. تخمین سود تا برقراری اتصال محاسبه نمی‌شود."

        logger.info(f"[CALC] سود روزانه: {daily_xmr:.6f} XMR | پیشرفت تا 0.001: {progress_pct:.1f}%")

        return {
            "clean_address": wallet,
            "cur_hash_str": f"{current_hashrate:,.1f}",
            "mean_hash_str": f"{mean_hashrate:,.1f} H/s",
            "balance_xmr": f"{balance_xmr:.6f}",
            "unconfirmed_xmr": f"{unconfirmed_xmr:.6f}",
            "paid_xmr": f"{paid_xmr:.6f}",
            "total_hashes": f"{total_hashes:,}",
            "workers": workers_list,
            "chart": [{"t": p["timestamp"], "hr": p["hashrate"]} for p in local_history],
            "progress_pct": f"{progress_pct:.1f}",
            "remaining_xmr": f"{remaining:.6f}",
            "payout_eta": payout_eta,
            "hourly_xmr": f"{hourly_xmr:.7f}",
            "daily_xmr": f"{daily_xmr:.6f}",
            "weekly_xmr": f"{weekly_xmr:.6f}",
            "monthly_xmr": f"{monthly_xmr:.5f}",
            "net_diff_str": f"{diff_billion:.2f} G" if diff_billion > 0 else "--",
            "diff_status": diff_status,
            "diff_explanation": diff_explanation,
            "est_share_sec": f"{est_share_sec}s" if est_share_sec > 0 else "--"
        }

# ==================== پل ارتباطی WebView ====================
class WebBridge:
    def __init__(self, saved_wallet):
        self.saved_wallet = saved_wallet
    def get_initial_wallet(self):
        return self.saved_wallet
    def get_stats(self, raw_address):
        return HashVaultEngine.fetch_telemetry(raw_address if raw_address else self.saved_wallet)

# ==================== کدهای فرانت‌اند شیشه مایع ====================
def generate_html(monero_b64, coin_b64, initial_wallet):
    m_img = f'<img src="{monero_b64}" class="w-logo"/>' if monero_b64 else '<div class="w-logo-fb">XMR</div>'
    c_img = f'<img src="{coin_b64}" class="w-logo"/>' if coin_b64 else '<div class="w-logo-fb">PRO</div>'

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monero Liquid Telemetry</title>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=Vazirmatn:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/vanilla-tilt/1.8.1/vanilla-tilt.min.js"></script>
    <style>
        *, *::before, *::after {{ margin:0; padding:0; box-sizing:border-box; }}
        :root {{
            --deep-ocean: #020713;
            --water-tint: rgba(255, 255, 255, 0.04);
            --water-border: rgba(255, 255, 255, 0.25);
            --water-border-top: rgba(255, 255, 255, 0.7);
            --neon-water: #00F0FF;
            --neon-blue: #0077FE;
            --text-main: #FFFFFF;
            --text-sub: #8EB3D8;
        }}
        body {{
            min-height: 100vh;
            background-color: var(--deep-ocean);
            color: var(--text-main);
            font-family: 'Vazirmatn', -apple-system, sans-serif;
            overflow-x: hidden;
            user-select: none;
            background-image: 
                radial-gradient(circle at 10% 10%, rgba(0, 119, 254, 0.3) 0%, transparent 40%),
                radial-gradient(circle at 90% 90%, rgba(0, 240, 255, 0.22) 0%, transparent 45%),
                radial-gradient(circle at 50% 50%, rgba(2, 10, 28, 0.9) 0%, var(--deep-ocean) 100%);
            background-attachment: fixed;
        }}
        .app-layout {{
            max-width: 1440px; margin: 0 auto; padding: 22px; display: flex; flex-direction: column; gap: 18px;
        }}
        .liquid-glass {{
            background: var(--water-tint);
            backdrop-filter: blur(28px) saturate(220%);
            -webkit-backdrop-filter: blur(28px) saturate(220%);
            border: 1px solid var(--water-border);
            border-top: 1.5px solid var(--water-border-top);
            border-radius: 22px;
            box-shadow: 0 20px 45px rgba(0, 8, 24, 0.6), inset 0 1.5px 3px rgba(255, 255, 255, 0.55), inset 0 -1.5px 3px rgba(0, 240, 255, 0.2);
            transition: all 0.3s ease;
        }}
        .liquid-glass:hover {{
            border-color: rgba(0, 240, 255, 0.5);
            box-shadow: 0 25px 60px rgba(0, 10, 32, 0.75), inset 0 1.5px 4px rgba(255, 255, 255, 0.7), inset 0 -1.5px 4px rgba(0, 240, 255, 0.35);
        }}
        .liquid-header {{
            display: flex; align-items: center; justify-content: space-between; padding: 14px 24px; gap: 16px; flex-wrap: wrap;
        }}
        .brand-cluster {{ display: flex; align-items: center; gap: 12px; }}
        .w-logo {{ width: 42px; height: 42px; object-fit: contain; filter: drop-shadow(0 0 12px rgba(0, 240, 255, 0.5)); }}
        .w-logo-fb {{
            width: 42px; height: 42px; border-radius: 12px; background: rgba(0, 240, 255, 0.12);
            border: 1px solid var(--water-border); display: flex; align-items: center; justify-content: center;
            font-weight: 800; font-size: 13px; color: var(--neon-water);
        }}
        .brand-meta h1 {{ font-size: 19px; font-weight: 800; }}
        .brand-meta h1 span {{ color: var(--neon-water); text-shadow: 0 0 14px var(--neon-water); }}
        .brand-meta p {{ font-size: 11px; color: var(--text-sub); }}
        .search-pill-bar {{ display: flex; align-items: center; gap: 10px; flex: 1; max-width: 650px; }}
        .input-water-shell {{
            flex: 1; background: rgba(2, 8, 20, 0.65); border: 1px solid var(--water-border);
            border-radius: 14px; display: flex; align-items: center; padding: 2px 14px;
        }}
        .input-water-shell input {{
            width: 100%; background: transparent; border: none; outline: none;
            color: #FFFFFF; font-family: 'JetBrains Mono', monospace; font-size: 13px; padding: 10px 0; direction: ltr;
        }}
        .btn-water {{
            background: linear-gradient(135deg, #00B4D8 0%, #0077FE 100%);
            border: 1px solid rgba(255, 255, 255, 0.4); color: #FFFFFF; border-radius: 12px;
            padding: 10px 22px; font-weight: 700; font-size: 13px; cursor: pointer;
            box-shadow: 0 6px 20px rgba(0, 119, 254, 0.45); transition: all 0.25s ease; white-space: nowrap;
        }}
        .btn-water:hover {{ filter: brightness(1.2); transform: translateY(-1px); }}
        .water-status {{ display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text-sub); }}
        .water-dot {{ width: 9px; height: 9px; border-radius: 50%; background: #FAAD14; }}
        .water-dot.active {{ background: #00F0FF; box-shadow: 0 0 14px #00F0FF; }}
        .water-dot.error {{ background: #FF4D4F; box-shadow: 0 0 14px #FF4D4F; }}
        .sync-timer-badge {{
            background: rgba(0, 240, 255, 0.1); border: 1px solid var(--water-border);
            padding: 4px 10px; border-radius: 10px; font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--neon-water);
        }}
        .payout-progress-banner {{ padding: 16px 24px; display: flex; flex-direction: column; gap: 8px; }}
        .progress-header {{ display: flex; justify-content: space-between; font-size: 13px; font-weight: 700; }}
        .progress-track {{
            width: 100%; height: 12px; background: rgba(2, 8, 20, 0.7); border: 1px solid var(--water-border);
            border-radius: 20px; overflow: hidden;
        }}
        .progress-fill {{
            height: 100%; width: 0%; background: linear-gradient(90deg, #0077FE 0%, #00F0FF 100%);
            box-shadow: 0 0 15px var(--neon-water); border-radius: 20px; transition: width 1s cubic-bezier(0.16, 1, 0.3, 1);
        }}
        .progress-footer {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--text-sub); }}
        .metric-cards-deck {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
        .card-inner-box {{ padding: 20px 22px; display: flex; flex-direction: column; justify-content: space-between; min-height: 150px; }}
        .card-meta-top {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--text-sub); }}
        .card-number-zone {{ margin: 8px 0; display: flex; align-items: baseline; gap: 8px; direction: ltr; text-align: right; }}
        .card-number-zone .num {{ font-family: 'JetBrains Mono', monospace; font-size: 28px; font-weight: 800; color: #FFFFFF; }}
        .card-number-zone .neon {{ color: var(--neon-water); text-shadow: 0 0 18px rgba(0, 240, 255, 0.5); }}
        .card-number-zone .unit {{ font-size: 13px; font-weight: 700; color: var(--text-sub); }}
        .card-meta-bot {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--text-sub); border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 8px; }}
        .card-meta-bot strong {{ color: #FFFFFF; font-family: 'JetBrains Mono', monospace; }}
        .analytics-deck {{ display: grid; grid-template-columns: 1.7fr 1fr; gap: 18px; }}
        @media (max-width: 1080px) {{ .analytics-deck {{ grid-template-columns: 1fr; }} }}
        .panel-header-wrap {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }}
        .panel-header-wrap h2 {{ font-size: 15px; font-weight: 700; }}
        .timeframe-selector {{
            display: flex; align-items: center; gap: 4px; background: rgba(2, 8, 20, 0.6);
            border: 1px solid var(--water-border); border-radius: 12px; padding: 3px;
        }}
        .tf-btn {{
            background: transparent; border: none; color: var(--text-sub); padding: 4px 10px;
            border-radius: 8px; font-size: 11px; font-weight: 700; font-family: 'JetBrains Mono', sans-serif;
            cursor: pointer; transition: all 0.2s ease;
        }}
        .tf-btn:hover {{ color: #FFF; background: rgba(255, 255, 255, 0.08); }}
        .tf-btn.active {{
            background: linear-gradient(135deg, #00B4D8 0%, #0077FE 100%);
            color: #FFFFFF; box-shadow: 0 0 10px rgba(0, 240, 255, 0.4);
        }}
        .chart-canvas-wrapper {{ position: relative; width: 100%; height: 260px; }}
        .calc-rows-container {{ display: flex; flex-direction: column; gap: 8px; }}
        .calc-liquid-row {{
            background: rgba(2, 8, 20, 0.55); border: 1px solid rgba(0, 240, 255, 0.12);
            border-radius: 12px; padding: 10px 14px; display: flex; justify-content: space-between; align-items: center; font-size: 13px;
        }}
        .calc-liquid-row .label {{ color: var(--text-sub); }}
        .calc-liquid-row .value {{ font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--neon-water); }}
        .diff-box {{
            background: rgba(2, 8, 20, 0.55); border: 1px solid rgba(0, 240, 255, 0.15);
            border-radius: 12px; padding: 12px; margin-top: 4px;
        }}
        .diff-title {{ font-size: 13px; font-weight: 700; color: var(--neon-water); margin-bottom: 4px; }}
        .diff-desc {{ font-size: 12px; color: var(--text-sub); line-height: 1.6; }}
        .table-shell {{ overflow-x: auto; }}
        .liquid-table {{ width: 100%; border-collapse: collapse; text-align: right; font-size: 13px; }}
        .liquid-table th {{ padding: 12px 18px; color: var(--text-sub); border-bottom: 1px solid rgba(255, 255, 255, 0.08); font-size: 11px; text-transform: uppercase; }}
        .liquid-table td {{ padding: 12px 18px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); font-family: 'JetBrains Mono', monospace; color: #FFFFFF; }}
        .tag-pill {{ display: inline-block; padding: 3px 8px; border-radius: 8px; font-size: 11px; font-weight: 700; }}
        .tag-pill.active {{ background: rgba(0, 240, 255, 0.15); color: var(--neon-water); border: 1px solid var(--water-border); }}
        .empty-placeholder {{ text-align: center !important; padding: 30px !important; color: var(--text-sub) !important; font-family: 'Vazirmatn', sans-serif !important; }}
    </style>
</head>
<body>
    <div class="app-layout">
        <header class="liquid-glass liquid-header">
            <div class="brand-cluster">
                {m_img}
                <div class="brand-meta">
                    <h1>MONERO <span>LIQUID</span> PRO</h1>
                    <p>پایش بلادرنگ هش‌ریت، تایم‌فریم اختصاصی و پایگاه داده محلی</p>
                </div>
            </div>
            <div class="search-pill-bar">
                <div class="input-water-shell">
                    <input type="text" id="wallet-input" value="{initial_wallet}" placeholder="آدرس والت یا لینک داشبورد مونرو..." spellcheck="false"/>
                </div>
                <button id="btn-sync" class="btn-water">استعلام زنده</button>
            </div>
            <div class="brand-cluster">
                <div class="water-status">
                    <span class="water-dot" id="status-dot"></span>
                    <span id="status-text">آماده دریافت</span>
                    <span class="sync-timer-badge" id="lbl-timer" title="زمان تا استعلام خودکار بعدی">05:00</span>
                </div>
                {c_img}
            </div>
        </header>

        <section class="liquid-glass payout-progress-banner">
            <div class="progress-header">
                <span>پیشرفت تا حداقل واریز استخر (کف پرداخت: ۰.۰۰۱ XMR)</span>
                <span id="lbl-progress-num" style="color: var(--neon-water);">۰.۰٪</span>
            </div>
            <div class="progress-track">
                <div class="progress-fill" id="bar-progress"></div>
            </div>
            <div class="progress-footer">
                <span>مقدار باقی‌مانده: <strong id="lbl-remaining-xmr" style="color:#FFF;">۰.۰۰۱۰۰۰ XMR</strong></span>
                <span id="lbl-payout-eta" style="color: var(--neon-water);">در حال محاسبه زمان...</span>
            </div>
        </section>

        <section class="metric-cards-deck">
            <div class="liquid-glass card-inner-box" data-tilt data-tilt-max="8" data-tilt-glare data-tilt-max-glare="0.3">
                <div class="card-meta-top">
                    <span>هش‌ریت لحظه‌ای ماینر</span>
                    <span>میانگین: <strong id="lbl-mean-hash">--</strong></span>
                </div>
                <div class="card-number-zone">
                    <span class="num neon" id="lbl-cur-hash">0.0</span>
                    <span class="unit">H/s</span>
                </div>
                <div class="card-meta-bot">
                    <span>ثبت هر سهم (Share):</span>
                    <strong id="lbl-share-interval">--</strong>
                </div>
            </div>

            <div class="liquid-glass card-inner-box" data-tilt data-tilt-max="8" data-tilt-glare data-tilt-max-glare="0.3">
                <div class="card-meta-top">
                    <span>موجودی تاییدشده (Confirmed)</span>
                    <span>هدف: 0.001 XMR</span>
                </div>
                <div class="card-number-zone">
                    <span class="num" id="lbl-balance">0.000000</span>
                    <span class="unit">XMR</span>
                </div>
                <div class="card-meta-bot">
                    <span>در انتظار تایید (Unconfirmed):</span>
                    <strong id="lbl-unconfirmed">0.000000 XMR</strong>
                </div>
            </div>

            <div class="liquid-glass card-inner-box" data-tilt data-tilt-max="8" data-tilt-glare data-tilt-max-glare="0.3">
                <div class="card-meta-top">
                    <span>کل درآمد پرداختی (Paid)</span>
                    <span>کل هش‌ها: <strong id="lbl-total-hashes">0</strong></span>
                </div>
                <div class="card-number-zone">
                    <span class="num" id="lbl-paid">0.000000</span>
                    <span class="unit">XMR</span>
                </div>
                <div class="card-meta-bot">
                    <span>تخمین سود ساعتی:</span>
                    <strong id="lbl-hourly-yield">0.0000000 XMR</strong>
                </div>
            </div>

            <div class="liquid-glass card-inner-box" data-tilt data-tilt-max="8" data-tilt-glare data-tilt-max-glare="0.3">
                <div class="card-meta-top">
                    <span>ریگ‌های فعال (Active Workers)</span>
                    <span id="lbl-rig-summary">0 آنلاین</span>
                </div>
                <div class="card-number-zone">
                    <span class="num" id="lbl-workers-count">0</span>
                    <span class="unit">دستگاه</span>
                </div>
                <div class="card-meta-bot">
                    <span>رکوردهای ثبت دیتابیس:</span>
                    <strong id="lbl-db-points">--</strong>
                </div>
            </div>
        </section>

        <section class="analytics-deck">
            <div class="liquid-glass" style="padding: 22px;">
                <div class="panel-header-wrap">
                    <h2>منحنی هش‌ریت (بازه دلخواه)</h2>
                    <div class="timeframe-selector">
                        <button class="tf-btn" data-tf="5m">5M</button>
                        <button class="tf-btn" data-tf="15m">15M</button>
                        <button class="tf-btn" data-tf="30m">30M</button>
                        <button class="tf-btn" data-tf="60m">1H</button>
                        <button class="tf-btn" data-tf="24h">24H</button>
                        <button class="tf-btn active" data-tf="all">ALL</button>
                    </div>
                </div>
                <div class="chart-canvas-wrapper">
                    <canvas id="hashChart"></canvas>
                </div>
            </div>

            <div class="liquid-glass" style="padding: 22px;">
                <div class="panel-header-wrap">
                    <h2>ماشین‌حساب زنده سودآوری و سختی</h2>
                </div>
                <div class="calc-rows-container">
                    <div class="calc-liquid-row">
                        <span class="label">تخمین درآمد روزانه (Daily):</span>
                        <span class="value" id="lbl-daily-yield">0.000000 XMR</span>
                    </div>
                    <div class="calc-liquid-row">
                        <span class="label">تخمین درآمد هفتگی (Weekly):</span>
                        <span class="value" id="lbl-weekly-yield">0.000000 XMR</span>
                    </div>
                    <div class="calc-liquid-row">
                        <span class="label">تخمین درآمد ماهانه (Monthly):</span>
                        <span class="value" id="lbl-monthly-yield">0.00000 XMR</span>
                    </div>
                    <div class="calc-liquid-row">
                        <span class="label">سختی شبکه (Difficulty):</span>
                        <span class="value" id="lbl-net-diff">--</span>
                    </div>
                    <div class="diff-box">
                        <div class="diff-title" id="lbl-diff-status">تحلیل وضعیت سختی شبکه</div>
                        <div class="diff-desc" id="lbl-diff-desc">در حال ارزیابی پارامترهای استخراج...</div>
                    </div>
                </div>
            </div>
        </section>

        <section class="liquid-glass" style="padding: 22px;">
            <div class="panel-header-wrap">
                <h2>فهرست ریگ‌ها و کارگرهای ماینینگ (Workers Telemetry)</h2>
            </div>
            <div class="table-shell">
                <table class="liquid-table">
                    <thead>
                        <tr>
                            <th>نام سیستم / ریگ</th>
                            <th>وضعیت</th>
                            <th>هش‌ریت لحظه‌ای</th>
                            <th>میانگین ۲۴ ساعته</th>
                            <th>آخرین هش ثبت‌شده</th>
                        </tr>
                    </thead>
                    <tbody id="workers-tbody">
                        <tr><td colspan="5" class="empty-placeholder">در حال ارتباط با پایگاه داده استخر...</td></tr>
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <script>
        let hashChart = null;
        let globalChartPoints = [];
        let currentTf = 'all';
        const SYNC_INTERVAL_SEC = 300;
        let countdownSeconds = SYNC_INTERVAL_SEC;

        function initChart() {{
            const ctx = document.getElementById('hashChart').getContext('2d');
            const grad = ctx.createLinearGradient(0, 0, 0, 260);
            grad.addColorStop(0, 'rgba(0, 240, 255, 0.45)');
            grad.addColorStop(0.7, 'rgba(0, 119, 254, 0.08)');
            grad.addColorStop(1, 'rgba(0, 240, 255, 0.0)');

            hashChart = new Chart(ctx, {{
                type: 'line',
                data: {{ labels: [], datasets: [{{ label: 'هش‌ریت (H/s)', data: [], borderColor: '#00F0FF', borderWidth: 2.4, backgroundColor: grad, fill: true, tension: 0.42, pointRadius: 2, pointHoverRadius: 6, pointHoverBackgroundColor: '#FFFFFF', pointHoverBorderColor: '#0077FE' }}] }},
                options: {{
                    responsive: true, maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ color: 'rgba(255, 255, 255, 0.04)' }}, ticks: {{ color: '#8EB3D8', font: {{ family: 'JetBrains Mono', size: 10 }} }} }},
                        y: {{ grid: {{ color: 'rgba(255, 255, 255, 0.05)' }}, ticks: {{ color: '#8EB3D8', font: {{ family: 'JetBrains Mono', size: 10 }} }} }}
                    }}
                }}
            }});
        }}

        function renderChartByTimeframe(tf) {{
            if (!hashChart || !globalChartPoints || globalChartPoints.length === 0) return;
            const now = Math.floor(Date.now() / 1000);
            let sec = Infinity;
            if (tf === '5m') sec = 300;
            else if (tf === '15m') sec = 900;
            else if (tf === '30m') sec = 1800;
            else if (tf === '60m') sec = 3600;
            else if (tf === '24h') sec = 86400;

            let filtered = globalChartPoints.filter(p => (now - p.t) <= sec);
            if (filtered.length < 2 && globalChartPoints.length >= 2) {{
                filtered = (tf === '5m') ? globalChartPoints.slice(-5) : ((tf === '15m') ? globalChartPoints.slice(-10) : globalChartPoints.slice(-25));
            }}

            const labels = [];
            const values = [];
            filtered.forEach(p => {{
                let d = new Date(p.t * 1000);
                labels.push((tf === '5m' || tf === '15m') ? d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0') + ':' + d.getSeconds().toString().padStart(2, '0') : d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0'));
                values.push(p.hr);
            }});

            hashChart.data.labels = labels;
            hashChart.data.datasets[0].data = values;
            hashChart.update();
        }}

        function updateUI(res) {{
            if (res.error) {{
                document.getElementById('status-dot').className = "water-dot error";
                document.getElementById('status-text').innerText = "خطا";
                alert(res.error);
                return;
            }}

            document.getElementById('status-dot').className = "water-dot active";
            document.getElementById('status-text').innerText = "متصل";

            document.getElementById('lbl-progress-num').innerText = res.progress_pct + '٪';
            document.getElementById('bar-progress').style.width = res.progress_pct + '%';
            document.getElementById('lbl-remaining-xmr').innerText = res.remaining_xmr + ' XMR';
            document.getElementById('lbl-payout-eta').innerText = res.payout_eta;

            document.getElementById('lbl-cur-hash').innerText = res.cur_hash_str;
            document.getElementById('lbl-mean-hash').innerText = res.mean_hash_str;
            document.getElementById('lbl-balance').innerText = res.balance_xmr;
            document.getElementById('lbl-unconfirmed').innerText = res.unconfirmed_xmr + " XMR";
            document.getElementById('lbl-paid').innerText = res.paid_xmr;
            document.getElementById('lbl-total-hashes').innerText = res.total_hashes;
            document.getElementById('lbl-workers-count').innerText = res.workers.length;
            document.getElementById('lbl-rig-summary').innerText = res.workers.length + " فعال";
            document.getElementById('lbl-share-interval').innerText = res.est_share_sec;
            document.getElementById('lbl-db-points').innerText = res.chart.length + " رکورد";

            document.getElementById('lbl-hourly-yield').innerText = res.hourly_xmr + " XMR";
            document.getElementById('lbl-daily-yield').innerText = res.daily_xmr + " XMR";
            document.getElementById('lbl-weekly-yield').innerText = res.weekly_xmr + " XMR";
            document.getElementById('lbl-monthly-yield').innerText = res.monthly_xmr + " XMR";
            document.getElementById('lbl-net-diff').innerText = res.net_diff_str;
            document.getElementById('lbl-diff-status').innerText = res.diff_status;
            document.getElementById('lbl-diff-desc').innerText = res.diff_explanation;

            const tbody = document.getElementById('workers-tbody');
            if (res.workers.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="5" class="empty-placeholder">هیچ ورکری آنلاین نیست.</td></tr>';
            }} else {{
                tbody.innerHTML = "";
                res.workers.forEach(w => {{
                    let tr = document.createElement('tr');
                    let diff = w.last_share ? Math.max(0, Math.floor(Date.now() / 1000) - w.last_share) : null;
                    let lastStr = diff !== null ? (diff < 60 ? diff + " ثانیه پیش" : Math.floor(diff / 60) + " دقیقه پیش") : "نامشخص";
                    tr.innerHTML = `<td style="font-weight:700;">${{w.name}}</td><td><span class="tag-pill active">فعال</span></td><td>${{w.hashrate}}</td><td>${{w.mean}}</td><td>${{lastStr}}</td>`;
                    tbody.appendChild(tr);
                }});
            }}

            globalChartPoints = res.chart || [];
            renderChartByTimeframe(currentTf);
            countdownSeconds = SYNC_INTERVAL_SEC;
        }}

        function syncData() {{
            const wallet = document.getElementById('wallet-input').value.trim();
            if (!wallet) return;
            document.getElementById('status-dot').className = "water-dot";
            document.getElementById('status-text').innerText = "در حال دریافت...";
            window.pywebview.api.get_stats(wallet).then(res => updateUI(res)).catch(() => {{
                document.getElementById('status-dot').className = "water-dot error";
                document.getElementById('status-text').innerText = "خطا";
            }});
        }}

        document.querySelectorAll('.tf-btn').forEach(btn => {{
            btn.addEventListener('click', () => {{
                document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentTf = btn.getAttribute('data-tf');
                renderChartByTimeframe(currentTf);
            }});
        }});

        function startCountdown() {{
            setInterval(() => {{
                countdownSeconds--;
                if (countdownSeconds <= 0) {{
                    countdownSeconds = SYNC_INTERVAL_SEC;
                    syncData();
                }}
                let m = Math.floor(countdownSeconds / 60).toString().padStart(2, '0');
                let s = (countdownSeconds % 60).toString().padStart(2, '0');
                document.getElementById('lbl-timer').innerText = m + ':' + s;
            }}, 1000);
        }}

        document.getElementById('btn-sync').addEventListener('click', syncData);
        document.getElementById('wallet-input').addEventListener('keypress', (e) => {{ if (e.key === 'Enter') syncData(); }});

        window.addEventListener('DOMContentLoaded', () => {{
            initChart();
            VanillaTilt.init(document.querySelectorAll("[data-tilt]"));
            startCountdown();
        }});

        window.addEventListener('pywebviewready', () => syncData());
    </script>
</body>
</html>
"""

# ==================== اجرای نهایی با WebView2 ====================
if __name__ == "__main__":
    saved_wallet = load_saved_wallet()
    monero_b64 = get_image_base64("monero")
    coin_b64 = get_image_base64("coin")

    html_code = generate_html(monero_b64, coin_b64, saved_wallet)

    temp_html_path = os.path.join(tempfile.gettempdir(), "monero_liquid_ui.html")
    with open(temp_html_path, "w", encoding="utf-8") as f:
        f.write(html_code)

    logger.info(f"فایل موقت UI ساخته شد: {temp_html_path}")
    bridge = WebBridge(saved_wallet)

    window = webview.create_window(
        title="Monero Liquid Telemetry Pro",
        url=f"file://{temp_html_path}",
        js_api=bridge,
        width=1340,
        height=870,
        min_size=(980, 680),
        background_color="#020713"
    )

    webview.start(debug=False)
