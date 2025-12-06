from playwright.sync_api import sync_playwright
import os, re
from PIL import Image
import time
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from datetime import datetime, timedelta
import threading

# --- CẤU HÌNH BOT TELEGRAM & ĐỊNH KỲ ---
TELEGRAM_BOT_TOKEN = "8397765740:AAHp2ZTsWifRo9jUguH2qv9EB9rnnoA0uW8"
TELEGRAM_CHAT_ID = "-1002455512034"
SEND_INTERVAL_SECONDS = 7200 # 2 giờ
# --- THÔNG ĐIỆP ĐÍNH KÈM ---
CAPTION_TEXT = "*🔥 KÈO THƠM HÔM NAY - VÀO NGAY KẺO LỠ ⚽️*\n\n🔗 [CƯỢC NGAY](https://vua99.com/?modal=SIGN_UP)"

# --- CẤU HÌNH WEB & ẢNH ---
URL = "https://keo.win/keo-bong-da"
# Sử dụng biến môi trường RAILWAY_VOLUME_MOUNT_PATH nếu có, hoặc /tmp
OUTPUT_DIR = os.path.join(os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/tmp"), "screenshots")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# File lưu trữ Message ID cuối cùng
LAST_MESSAGE_ID_FILE = os.path.join(OUTPUT_DIR, "last_message_id.txt") 

FIXED_HEADER_CLIP = {'x':200, 'y': 800, 'width':800, 'height': 68}
TEMP_HEADER_PATH = os.path.join(OUTPUT_DIR, "fixed_header_clip.png")
LOGO_PATH = os.path.join(os.getcwd(), "logo.png")
LOGO_POSITION = (600, 60)
LOGO_SIZE = (80,50)

LEAGUE_HEADER_SELECTOR = ".w-full.bg-\\[\\#e0e6f4\\].text-header-bottom.text-\\[14px\\].leading-\\[22px\\].font-bold.h-\\[34px\\].flex.items-center.px-\\[10px\\]"
MATCH_ROW_SELECTOR = ".bg-row-background"

# --- DANH SÁCH ƯU TIÊN ---
MATCHES_TO_KEEP = [
    "FIFA World Cup", "UEFA European Championship", "Copa América", "UEFA Champions League", 
    "UEFA Europa League", "Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1", 
    "Olympic Football Tournament", "FA Cup", "Copa del Rey", "Coppa Italia", "DFB-Pokal", 
    "UEFA Europa Conference League", "EFL Championship", "Africa Cup of Nations", "CONCACAF Gold Cup", 
    "AFC Asian Cup", "MLS", "Saudi Pro League", "FIFA World Cup Qualifiers", "AFC U23 Asian Cup", 
    "AFC Champions League", "AFF Mitsubishi Electric Cup", "AFF U23 Championship", "SEA Games Football", 
    "V.League 1", "V.League 2", "AFC Cup", "FA Community Shield", "EFL Cup", "UEFA Super Cup", "Seagames"
]

# --- CACHE (Bộ nhớ đệm) ĐỂ KIỂM TRA ĐÃ GỬI CHƯA ---
SENT_LEAGUES_CACHE = {} 
CACHE_EXPIRY_SECONDS = 86400 # 24 giờ
CACHE_LOCK = threading.Lock() 

# ----------------------------------------------------------------------
# HÀM HỖ TRỢ CHUNG VÀ CACHE
# ----------------------------------------------------------------------

def sanitize(name):
    """Loại bỏ ký tự không hợp lệ trong tên file"""
    return re.sub(r'[\\/*?:"<>|]', "_", name)

def get_league_name_from_element(league_element, idx):
    """Lấy tên giải đấu từ phần tử HTML"""
    title_el = league_element.query_selector(LEAGUE_HEADER_SELECTOR)
    # Lấy tên giải đấu, cắt bỏ ngày giờ và các ký tự đặc biệt ở cuối (nếu có)
    name = title_el.inner_text().strip() if title_el else f"league_{idx}"
    name = re.sub(r'\s*(\d{2}/\d{2}|\d{2}/\d{2}\s*-\s*\d{2}/\d{2}|\(\d{2}/\d{2}\s*-\s*\d{2}/\d{2}\))', '', name).strip()
    return name

def is_league_already_sent(sanitized_league_name):
    """Kiểm tra xem giải đấu đã được gửi trong khoảng thời gian hết hạn chưa."""
    with CACHE_LOCK:
        if sanitized_league_name in SENT_LEAGUES_CACHE:
            expiry_time = SENT_LEAGUES_CACHE[sanitized_league_name]
            if datetime.now() < expiry_time:
                return True
            else:
                # Xóa mục đã hết hạn
                del SENT_LEAGUES_CACHE[sanitized_league_name]
        return False

def mark_league_as_sent(sanitized_league_name):
    """Đánh dấu giải đấu là đã gửi và thiết lập thời gian hết hạn."""
    with CACHE_LOCK:
        expiry_time = datetime.now() + timedelta(seconds=CACHE_EXPIRY_SECONDS)
        SENT_LEAGUES_CACHE[sanitized_league_name] = expiry_time
        print(f"-> Đã đánh dấu '{sanitized_league_name}' là đã gửi. Hết hạn: {expiry_time.strftime('%H:%M:%S')}")

def capture_fixed_header(page, clip_rect, output_path):
    """Chụp màn hình một khu vực cố định (tọa độ tuyệt đối) trên trang đã load."""
    if clip_rect["width"] <= 0 or clip_rect["height"] <= 0:
        print("❌ Clip Header cố định không hợp lệ.")
        return False
        
    try:
        page.screenshot(path=output_path, clip=clip_rect)
        return True
    except Exception as e:
        print(f"❌ Lỗi khi chụp Header cố định: {e}")
        return False

def stitch_images(base_path, header_path, logo_path, output_path, logo_size, logo_pos):
    """Ghép header lên trên ảnh chụp giải đấu và logo."""
    try:
        base_img = Image.open(base_path)
        header_img = Image.open(header_path)
        logo_img = Image.open(logo_path)

        header_img = header_img.resize((base_img.width, header_img.height))

        new_width = base_img.width
        new_height = base_img.height + header_img.height

        stitched_img = Image.new('RGB', (new_width, new_height), color='white')

        stitched_img.paste(header_img, (0, 0))
        stitched_img.paste(base_img, (0, header_img.height))

        logo_img = logo_img.resize(logo_size)
        if logo_img.mode == 'RGBA':
            stitched_img.paste(logo_img, logo_pos, logo_img)
        else:
            stitched_img.paste(logo_img, logo_pos)

        stitched_img.save(output_path)
        print(f"✔ Đã ghép thành công và lưu tại: {output_path}")
        return True
    except FileNotFoundError as e:
        print(f"❌ Lỗi FileNotFoundError: {e}")
        return False
    except Exception as e:
        print(f"❌ Lỗi khi xử lý ảnh: {e}")
        return False

# ----------------------------------------------------------------------
# HÀM HỖ TRỢ XÓA TIN NHẮN (LƯU TRỮ TRẠNG THÁI)
# ----------------------------------------------------------------------

def read_last_message_id():
    """Đọc Message ID cuối cùng đã gửi từ file."""
    if os.path.exists(LAST_MESSAGE_ID_FILE):
        try:
            with open(LAST_MESSAGE_ID_FILE, 'r') as f:
                return int(f.read().strip())
        except Exception:
            return None
    return None

def save_last_message_id(message_id):
    """Lưu Message ID mới nhất vào file."""
    try:
        with open(LAST_MESSAGE_ID_FILE, 'w') as f:
            f.write(str(message_id))
        print(f"-> Đã lưu Message ID mới: {message_id}")
    except Exception as e:
        print(f"❌ Lỗi khi lưu Message ID: {e}")

async def delete_last_message(bot, chat_id):
    """Xóa tin nhắn cũ đã được lưu."""
    message_id = read_last_message_id()
    if message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            print(f"✅ Đã xóa tin nhắn cũ có ID: {message_id}")
        except TelegramError as e:
            # Lỗi 400 Bad Request (Message to delete not found) là phổ biến và có thể bỏ qua
            if "message to delete not found" in str(e).lower() or "bad request: message can't be deleted" in str(e).lower():
                 print(f"⚠️ Tin nhắn cũ ID {message_id} không tồn tại hoặc không thể xóa.")
            else:
                print(f"❌ Lỗi khi xóa tin nhắn Telegram: {e}")
        except Exception as e:
             print(f"❌ Lỗi không xác định khi xóa tin nhắn: {e}")


# --- HÀM LOGIC CHÍNH PLAYWRIGHT (Đồng bộ) ---

def capture_and_stitch_core(p):
    """Chụp ảnh giải đấu và ghép với Header cố định. Trả về đường dẫn file ảnh cuối cùng."""
    browser = None
    temp_filepath = "" 
    target_league_name = None
    
    try:
        browser = p.chromium.launch(headless=True) 
        page = browser.new_page(viewport={"width": 1600, "height": 3000})
        page.goto(URL)
        page.wait_for_load_state("networkidle", timeout=30000) 

        if not capture_fixed_header(page, FIXED_HEADER_CLIP, TEMP_HEADER_PATH):
            return None
        
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(2000) 
        time.sleep(1) 

        leagues = page.query_selector_all('[class="flex flex-col"]')
        
        target_league = None 
        
        # 1. Tìm giải đấu ưu tiên
        for idx, league in enumerate(leagues):
            league_name = get_league_name_from_element(league, idx)
            sanitized_name = sanitize(league_name) 
            league.scroll_into_view_if_needed()
            time.sleep(0.3) 
            
            # BỎ QUA các giải đấu đã được gửi trong 24 giờ qua
            if is_league_already_sent(sanitized_name):
                print(f"⚠️ Bỏ qua: Giải đấu '{league_name}' đã được gửi trong 24h qua.")
                continue

            # Kiểm tra tên giải đấu có trong danh sách ưu tiên không
            if any(m.lower() in league_name.lower() for m in MATCHES_TO_KEEP):
                target_league = league
                target_league_name = sanitized_name + "_Prioritized"
                break 
        
        # 2. Nếu không tìm thấy giải ưu tiên CHƯA GỬI, chọn giải đầu tiên CHƯA GỬI
        if target_league is None:
            for idx, league in enumerate(leagues):
                league_name = get_league_name_from_element(league, idx)
                sanitized_name = sanitize(league_name)
                
                if not is_league_already_sent(sanitized_name):
                    target_league = league
                    target_league_name = sanitized_name + "_FirstOnWeb"
                    break
                else:
                    pass 

        if target_league:
            target_league.scroll_into_view_if_needed()
            page.wait_for_timeout(1000) 
            
            # --- LOGIC TÍNH TOÁN BOUNDING BOX ---
            title_el = target_league.query_selector(LEAGUE_HEADER_SELECTOR)
            match_rows = target_league.query_selector_all(MATCH_ROW_SELECTOR) 
            
            all_boxes = []
            title_box = None

            if title_el:
                title_box = title_el.bounding_box()
                if title_box and title_box["width"] > 0 and title_box["height"] > 0:
                    all_boxes.append(title_box)

            for m in match_rows:
                box = m.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    all_boxes.append(box)

            if not all_boxes:
                print(f"⚠ Bỏ qua {target_league_name}, không lấy được bounding box nào.")
                return None
            
            x0 = min(b["x"] for b in all_boxes)
            y0 = min(b["y"] for b in all_boxes)
            x1 = max(b["x"] + b["width"] for b in all_boxes)
            y1 = max(b["y"] + b["height"] for b in all_boxes)

            if len(match_rows) == 0 and title_box:
                y1 += 50

            clip_rect = {
                "x": 200, 
                "y": max(0, y0),
                "width": 800, 
                "height": max(1, y1 - y0)
            }
            
            if clip_rect["width"] > 0 and clip_rect["height"] > 0:
                temp_filepath = os.path.join(OUTPUT_DIR, f"TEMP_{target_league_name}.png")
                
                # Chụp ảnh nội dung chính
                page.screenshot(path=temp_filepath, clip=clip_rect)
                
                final_filepath = os.path.join(OUTPUT_DIR, f"{target_league_name}_FINAL.png")
                
                if stitch_images(temp_filepath, TEMP_HEADER_PATH, LOGO_PATH, final_filepath, LOGO_SIZE, LOGO_POSITION):
                    # Đánh dấu đã gửi thành công trước khi trả về đường dẫn file
                    mark_league_as_sent(sanitize(get_league_name_from_element(target_league, 0)))
                    return final_filepath
                else:
                    return None
            else:
                return None
        else:
            print("⚠️ Bỏ qua chu kỳ: Không tìm thấy giải đấu nào để gửi (hoặc tất cả đã được gửi).")
            return None

    except Exception as e:
        print(f"❌ Lỗi trong Playwright Core: {e}")
        return None
    finally:
        if browser:
            browser.close()
            
# ----------------------------------------------------------------------
# HÀM WRAPPER (Đồng bộ) và TELEGRAM (Bất đồng bộ)
# ----------------------------------------------------------------------

def capture_and_stitch_wrapper():
    """Hàm bọc đồng bộ để chạy Playwright Sync API."""
    try:
        with sync_playwright() as p:
            return capture_and_stitch_core(p)
    except Exception as e:
        print(f"❌ LỖI TRONG PLAYWRIGHT WRAPPER: {e}")
        return None

async def send_to_telegram_periodically():
    """Vòng lặp định kỳ chụp ảnh và gửi qua Telegram."""
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    while True:
        start_time = time.time()
        print(f"\n[{time.strftime('%H:%M:%S')}] Bắt đầu chu kỳ chụp ảnh...")
        final_image_path = None
        
        try:
            # 1. Xóa tin nhắn cũ (nếu có)
            await delete_last_message(bot, TELEGRAM_CHAT_ID)
            
            # 2. Chụp và ghép ảnh mới
            final_image_path = await asyncio.to_thread(capture_and_stitch_wrapper)

            if final_image_path and os.path.exists(final_image_path):
                print(f"✨ Đã hoàn thành ghép ảnh: {final_image_path}")
                
                # 3. Gửi ảnh mới qua Telegram
                with open(final_image_path, 'rb') as photo_file:
                    message = await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=photo_file,
                        caption=CAPTION_TEXT, 
                        parse_mode='Markdown' 
                    )
                print(f"✅ Đã gửi ảnh thành công qua Telegram. ID: {message.message_id}")
                
                # 4. Lưu Message ID mới để xóa trong chu kỳ tiếp theo
                save_last_message_id(message.message_id)
                
                # Xóa file sau khi gửi thành công
                os.remove(final_image_path)
                print(f"Đã xóa file cuối: {final_image_path}")
                
            else:
                print("⚠️ Bỏ qua chu kỳ: Không tìm thấy giải đấu mới hoặc ảnh bị lỗi. Giữ tin cũ.")

        except TelegramError as e:
            print(f"❌ LỖI TELEGRAM: {e}")
        except Exception as e:
            print(f"❌ LỖI KHÔNG XÁC ĐỊNH: {e}")

        finally:
            # Dọn dẹp các file tạm
            if os.path.exists(TEMP_HEADER_PATH):
                os.remove(TEMP_HEADER_PATH)
            
            temp_files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith("TEMP_") and f.endswith(".png")]
            for temp_f in temp_files:
                try:
                    os.remove(os.path.join(OUTPUT_DIR, temp_f))
                except Exception as e:
                    print(f"Lỗi khi xóa file tạm {temp_f}: {e}")
                    
        end_time = time.time()
        elapsed_time = end_time - start_time
        
        wait_time = max(0, SEND_INTERVAL_SECONDS - elapsed_time)
        print(f"🕰️ Chu kỳ hoàn thành trong {elapsed_time:.2f}s. Chờ {wait_time:.2f}s cho chu kỳ tiếp theo.")
        await asyncio.sleep(wait_time) 


if __name__ == "__main__":
    print("🚀 Bắt đầu Bot gửi kèo (Chu kỳ 2h)...")
    try:
        asyncio.run(send_to_telegram_periodically())
    except KeyboardInterrupt:
        print("\n👋 Đã dừng chương trình.")
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
             print("\n👋 Đã dừng chương trình (Lỗi Event loop đóng đã được xử lý).")
        else:
             print(f"❌ Lỗi Runtime không xác định: {e}")


