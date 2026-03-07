from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import pandas as pd
from io import BytesIO
import re
from datetime import datetime

ADMIN_CHAT_ID = 887019439
LABEL_SEP = r"\s*[:\-]\s*"
get_digits = lambda s: re.sub(r"\D", "", s or "")

# ---------------- Helpers ----------------
def normalize_payment(value: str, full_text: str) -> str:
    txt = (value or "").strip()
    hay = (txt or full_text).upper()
    if "COD" in hay:
        return "COD"
    if "TF" in hay or "TRANSFER" in hay:
        return "Transfer"
    if "TUNAI" in hay or "CASH" in hay:
        return "Tunai"
    return txt or "(Tidak Diketahui)"

def grab(pattern: str, text: str):
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None

# Header produk, contoh: "1. anet ( 1 biggy)"
PRODUCT_HEADER_RE = re.compile(r"^\s*\d+\.\s*([A-Za-zÀ-ÖØ-öø-ÿ]+)\s*\(([^)]+)\)", re.IGNORECASE | re.MULTILINE)

def extract_product_name(text: str, jumlah_order: int) -> str | None:
    m = PRODUCT_HEADER_RE.search(text or "")
    if not m:
        return None
    name_after_number = (m.group(1) or "").strip()
    in_paren = (m.group(2) or "").strip()
    two = name_after_number[:2].lower() if name_after_number else ""
    # Bersihkan angka di dalam kurung: (1biggy) -> biggy
    prod = re.sub(r"^\s*\d+\s*", "", in_paren)
    if not prod:
        return None
    prod_title = prod[:1].upper() + prod[1:]
    suffix = f" ({two})" if two else ""
    return (f"{jumlah_order} Pcs {prod_title}{suffix}" if jumlah_order and jumlah_order > 1
            else f"{prod_title}{suffix}")

# ---------------- Store ----------------
user_data = {}

# ---------------- Commands ----------------
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Chat ID kamu adalah: {chat_id}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot siap menerima data order.")

# ---------------- Core Parser ----------------
async def save_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    # Pola fleksibel
    tipe_pat    = rf"(?:tipe|type)\s*pembayaran{LABEL_SEP}(.+)"
    nama_pat    = rf"(?:nama(?:\s*lengkap)?|panggilan)" + LABEL_SEP + r"(.+)"
    hp_pat      = rf"(?:nomor|no\.?)[\s\-]*?(?:hp|wa)(?:\s*\/\s*(?:hp|wa))?{LABEL_SEP}(.+)"
    kec_pat     = rf"(?:kecamatan|kec)\b{LABEL_SEP}(.+)"
    # Alamat (terima 'alamat', 'alamat l', 'alamat lengkap')
    alamat_one_line_pat  = rf"alamat(?:\s*lengkap|\s*l)?{LABEL_SEP}(.+)"
    # Patokan/RT-RW/variasi lain
    rtrw_pat    = rf"(?:rt\s*rw\s*\/\s*patokan\s*rumah|rt\s*\/\s*rw|rt\s*rw|rt)\b{LABEL_SEP}(.+)"
    patokan_pat = rf"(?:patokan(?:\s*rumah)?)\b{LABEL_SEP}(.+)"
    kel_pat     = rf"(?:kelurahan|desa|kel\.|ds\.){LABEL_SEP}(.+)"
    kab_pat     = rf"(?:kabupaten|kab\.){LABEL_SEP}(.+)"
    prov_pat    = rf"(?:provinsi|prov\.){LABEL_SEP}(.+)"
    jumlah_pat  = rf"(?:jumlah(?:\s*order)?|jumblah|jml|order){LABEL_SEP}(.+)"
    total_pat   = rf"(?:total|harga){LABEL_SEP}(.+)"
    note_pat    = rf"(?:catatan|notes?|note|intruksi|instruksi){LABEL_SEP}(.+)"

    tipe_val      = normalize_payment(grab(tipe_pat, text), text)
    nama_val      = grab(nama_pat, text)
    hp_digits     = get_digits(grab(hp_pat, text))
    kec_val       = grab(kec_pat, text)
    alamat_val    = grab(alamat_one_line_pat, text)
    rtrw_val      = grab(rtrw_pat, text)
    patokan_val   = grab(patokan_pat, text)
    kel_val       = grab(kel_pat, text)
    kab_val       = grab(kab_pat, text)
    prov_val      = grab(prov_pat, text)
    jumlah_digits = get_digits(grab(jumlah_pat, text))
    total_digits  = get_digits(grab(total_pat, text))
    note_val      = grab(note_pat, text)

    # --- Blok: tangkap SEMUA teks setelah label 'alamat' (multi-baris hingga akhir) ---
    alamat_after = None
    m_after = re.search(r"alamat(?:\s*lengkap|\s*l)?\s*[:\-]\s*", text, flags=re.IGNORECASE)
    if m_after:
        alamat_after = text[m_after.end():]

    # Bangun parts alamat
    alamat_parts = []

    # 1) Nilai awal setelah 'alamat' (jika ada di baris yang sama)
    if alamat_val:
        alamat_parts.append(alamat_val)

    # 2) Label spesifik
    if kel_val: alamat_parts.append(f"Kel/Desa {kel_val}")
    if kec_val: alamat_parts.append(f"Kec. {kec_val}")
    if kab_val: alamat_parts.append(f"Kab. {kab_val}")
    if prov_val: alamat_parts.append(f"Prov. {prov_val}")

    # 3) RT/RW (opsional) – kita masukkan sebagai bagian normal alamat
    if rtrw_val: alamat_parts.append(rtrw_val)

    # 4) Tambah baris-baris bebas setelah 'alamat' yang tidak diawali label lain
    if alamat_after:
        # Pisahkan per baris, ambil yang bukan label umum dan bukan kosong
        label_start_re = re.compile(r"^\s*(?:kelurahan|desa|kel\.|ds\.|kecamatan|kec|kabupaten|kab\.|provinsi|prov\.|catatan|notes?|note|intruksi|instruksi|nomor|no\.?\s*(?:hp|wa)|jumlah|jumblah|jml|order|total|harga)\b",
                                    re.IGNORECASE)
        for line in alamat_after.splitlines():
            ln = line.strip()
            if not ln:
                continue
            if label_start_re.search(ln):
                continue
            # Hindari duplikasi jika sudah ada dalam parts
            if ln not in alamat_parts:
                alamat_parts.append(ln)

    # Gabungkan alamat
    alamat_lengkap_bersusun = ", ".join([p.strip() for p in alamat_parts if p and p.strip()])

    # --- Validasi kelengkapan ---
    missing = []
    if not tipe_val: missing.append("Tipe Pembayaran")
    if not nama_val: missing.append("Nama")
    if not hp_digits: missing.append("Nomor HP")
    if not (kec_val): missing.append("Kecamatan")
    if not (alamat_lengkap_bersusun or alamat_val or kel_val or kab_val or prov_val): missing.append("Alamat")
    if not jumlah_digits: missing.append("Jumlah Order")
    if not total_digits: missing.append("Total")
    if missing:
        await update.message.reply_text("⚠️ Data belum lengkap: " + ", ".join(missing))
        return

    jumlah_int = int(jumlah_digits)
    if len(hp_digits) < 10:
        await update.message.reply_text("⚠️ Nomor HP minimal 10 digit.")
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import pandas as pd
from io import BytesIO
import re
from datetime import datetime

ADMIN_CHAT_ID = 887019439
LABEL_SEP = r"\s*[:\-]\s*"
get_digits = lambda s: re.sub(r"\D", "", s or "")

# ---------------- Helpers ----------------
def normalize_payment(value: str, full_text: str) -> str:
    txt = (value or "").strip()
    hay = (txt or full_text).upper()
    if "COD" in hay:
        return "COD"
    if "TF" in hay or "TRANSFER" in hay:
        return "Transfer"
    if "TUNAI" in hay or "CASH" in hay:
        return "Tunai"
    return txt or "(Tidak Diketahui)"

def grab(pattern: str, text: str):
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None

# Header produk, contoh: "1. anet ( 1 biggy)"
PRODUCT_HEADER_RE = re.compile(r"^\s*\d+\.\s*([A-Za-zÀ-ÖØ-öø-ÿ]+)\s*\(([^)]+)\)", re.IGNORECASE | re.MULTILINE)

def extract_product_name(text: str, jumlah_order: int) -> str | None:
    m = PRODUCT_HEADER_RE.search(text or "")
    if not m:
        return None
    name_after_number = (m.group(1) or "").strip()
    in_paren = (m.group(2) or "").strip()
    two = name_after_number[:2].lower() if name_after_number else ""
    # Bersihkan angka di dalam kurung: (1biggy) -> biggy
    prod = re.sub(r"^\s*\d+\s*", "", in_paren)
    if not prod:
        return None
    prod_title = prod[:1].upper() + prod[1:]
    suffix = f" ({two})" if two else ""
    return (f"{jumlah_order} Pcs {prod_title}{suffix}" if jumlah_order and jumlah_order > 1
            else f"{prod_title}{suffix}")

# ---------------- Store ----------------
user_data = {}

# ---------------- Commands ----------------
async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Chat ID kamu adalah: {chat_id}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot siap menerima data order.")

# ---------------- Core Parser ----------------
async def save_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""

    # Pola fleksibel
    tipe_pat    = rf"(?:tipe|type)\s*pembayaran{LABEL_SEP}(.+)"
    nama_pat    = rf"(?:nama(?:\s*lengkap)?|panggilan)" + LABEL_SEP + r"(.+)"
    hp_pat      = rf"(?:nomor|no\.?)[\s\-]*?(?:hp|wa)(?:\s*\/\s*(?:hp|wa))?{LABEL_SEP}(.+)"
    kec_pat     = rf"(?:kecamatan|kec)\b{LABEL_SEP}(.+)"
    # Alamat (terima 'alamat', 'alamat l', 'alamat lengkap')
    alamat_one_line_pat  = rf"alamat(?:\s*lengkap|\s*l)?{LABEL_SEP}(.+)"
    # Patokan/RT-RW/variasi lain
    rtrw_pat    = rf"(?:rt\s*rw\s*\/\s*patokan\s*rumah|rt\s*\/\s*rw|rt\s*rw|rt)\b{LABEL_SEP}(.+)"
    patokan_pat = rf"(?:patokan(?:\s*rumah)?)\b{LABEL_SEP}(.+)"
    kel_pat     = rf"(?:kelurahan|desa|kel\.|ds\.){LABEL_SEP}(.+)"
    kab_pat     = rf"(?:kabupaten|kab\.){LABEL_SEP}(.+)"
    prov_pat    = rf"(?:provinsi|prov\.){LABEL_SEP}(.+)"
    jumlah_pat  = rf"(?:jumlah(?:\s*order)?|jumblah|jml|order){LABEL_SEP}(.+)"
    total_pat   = rf"(?:total|harga){LABEL_SEP}(.+)"
    note_pat    = rf"(?:catatan|notes?|note|intruksi|instruksi){LABEL_SEP}(.+)"

    tipe_val      = normalize_payment(grab(tipe_pat, text), text)
    nama_val      = grab(nama_pat, text)
    hp_digits     = get_digits(grab(hp_pat, text))
    kec_val       = grab(kec_pat, text)
    alamat_val    = grab(alamat_one_line_pat, text)
    rtrw_val      = grab(rtrw_pat, text)
    patokan_val   = grab(patokan_pat, text)
    kel_val       = grab(kel_pat, text)
    kab_val       = grab(kab_pat, text)
    prov_val      = grab(prov_pat, text)
    jumlah_digits = get_digits(grab(jumlah_pat, text))
    total_digits  = get_digits(grab(total_pat, text))
    note_val      = grab(note_pat, text)

    # --- Blok: tangkap SEMUA teks setelah label 'alamat' (multi-baris hingga akhir) ---
    alamat_after = None
    m_after = re.search(r"alamat(?:\s*lengkap|\s*l)?\s*[:\-]\s*", text, flags=re.IGNORECASE)
    if m_after:
        alamat_after = text[m_after.end():]

    # Bangun parts alamat
    alamat_parts = []

    # 1) Nilai awal setelah 'alamat' (jika ada di baris yang sama)
    if alamat_val:
        alamat_parts.append(alamat_val)

    # 2) Label spesifik
    if kel_val: alamat_parts.append(f"Kel/Desa {kel_val}")
    if kec_val: alamat_parts.append(f"Kec. {kec_val}")
    if kab_val: alamat_parts.append(f"Kab. {kab_val}")
    if prov_val: alamat_parts.append(f"Prov. {prov_val}")

    # 3) RT/RW (opsional) – kita masukkan sebagai bagian normal alamat
    if rtrw_val: alamat_parts.append(rtrw_val)

    # 4) Tambah baris-baris bebas setelah 'alamat' yang tidak diawali label lain
    if alamat_after:
        # Pisahkan per baris, ambil yang bukan label umum dan bukan kosong
        label_start_re = re.compile(r"^\s*(?:kelurahan|desa|kel\.|ds\.|kecamatan|kec|kabupaten|kab\.|provinsi|prov\.|catatan|notes?|note|intruksi|instruksi|nomor|no\.?\s*(?:hp|wa)|jumlah|jumblah|jml|order|total|harga)\b",
                                    re.IGNORECASE)
        for line in alamat_after.splitlines():
            ln = line.strip()
            if not ln:
                continue
            if label_start_re.search(ln):
                continue
            # Hindari duplikasi jika sudah ada dalam parts
            if ln not in alamat_parts:
                alamat_parts.append(ln)

    # Gabungkan alamat
    alamat_lengkap_bersusun = ", ".join([p.strip() for p in alamat_parts if p and p.strip()])

    # --- Validasi kelengkapan ---
    missing = []
    if not tipe_val: missing.append("Tipe Pembayaran")
    if not nama_val: missing.append("Nama")
    if not hp_digits: missing.append("Nomor HP")
    if not (kec_val): missing.append("Kecamatan")
    if not (alamat_lengkap_bersusun or alamat_val or kel_val or kab_val or prov_val): missing.append("Alamat")
    if not jumlah_digits: missing.append("Jumlah Order")
    if not total_digits: missing.append("Total")
    if missing:
        await update.message.reply_text("⚠️ Data belum lengkap: " + ", ".join(missing))
        return

    jumlah_int = int(jumlah_digits)
    if len(hp_digits) < 10:
        await update.message.reply_text("⚠️ Nomor HP minimal 10 digit.")
        return
    if jumlah_int < 1:
        await update.message.reply_text("⚠️ Jumlah order minimal 1.")
        return
    total_int = int(total_digits)
    if total_int < 10000 or total_int > 1000000:
        await update.message.reply_text("⚠️ Total harus antara 10.000 dan 1.000.000.")
        return

    # Nama Produk dari header + jumlah
    nama_produk = extract_product_name(text, jumlah_int)

    # Patokan ditaruh di akhir dalam tanda kurung (jika ada)
    final_alamat = alamat_lengkap_bersusun
    if patokan_val:
        if final_alamat:
            final_alamat = f"{final_alamat} ({patokan_val})"
        else:
            final_alamat = f"({patokan_val})"

    record = {
        "Tipe Pembayaran": tipe_val,
        "Nama": nama_val.strip(),
        "Nomor HP": hp_digits,
        "Kecamatan": (kec_val or "").strip(),
        "Alamat Lengkap": final_alamat or (alamat_val or "").strip(),
        "Jumlah Order": str(jumlah_int),
        "Total": str(total_int),
        "Catatan": (note_val or "").strip() if note_val else "",
        "Nama Produk": nama_produk or "",
        "Tanggal Input": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    user_data.setdefault(user_id, []).append(record)
    await update.message.reply_text("✅ Data berhasil disimpan.")

    if ADMIN_CHAT_ID:
        lines = [
            "📦 Data Baru Masuk:",
            f"Tipe Pembayaran: {record['Tipe Pembayaran']}",
            f"Nama: {record['Nama']}",
            f"Nomor HP: {record['Nomor HP']}",
            f"Kecamatan: {record['Kecamatan']}",
            f"Alamat Lengkap: {record['Alamat Lengkap']}",
            f"Jumlah Order: {record['Jumlah Order']}",
            f"Total: {record['Total']}",
            f"Catatan: {record['Catatan']}",
            f"Nama Produk: {record['Nama Produk']}",
            f"Tanggal Input: {record['Tanggal Input']}",
        ]
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="\n".join(lines))

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id, [])
    if not data:
        await update.message.reply_text("Belum ada data yang disimpan.")
        return

    cols = [
        "Tipe Pembayaran", "Nama", "Nomor HP", "Kecamatan",
        "Alamat Lengkap", "Jumlah Order", "Total", "Catatan", "Nama Produk", "Tanggal Input"
    ]
    df = pd.DataFrame(data, columns=cols)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    output.seek(0)

    filename = f"Shopperia data {datetime.now().strftime('%Y-%m-%d %H.%M')}.xlsx"
    await update.message.reply_document(document=InputFile(output, filename=filename))
    user_data[user_id] = []

# ---------------- Main ----------------
def main():    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getid", get_chat_id))
    app.add_handler(CommandHandler("export", export_excel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_data))
    app.run_polling()

if __name__ == "__main__":
    main()

