import logging
import re
import time
import threading
import json
import hashlib
import http.server
import socketserver
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler,
    PicklePersistence  # Added for memory stability
)

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SYSTEM STATE ---
_secret = "0127"
DASHBOARD_PASSWORD_HASH = hashlib.sha256(_secret.encode()).hexdigest()

BOT_ACTIVE = True 
DELIVERY_CHARGE = 60
BOT_START_TIME = time.time()
STATS = {"total_orders": 0, "total_revenue": 0}
PORT = int(os.environ.get("PORT", 10000))

# --- INVENTORY DATA ---
CP_SIZES = ["6/8", "8/10", "9/12", "10/14", "12/16", "14/18", "16/20", "18/24"]
PCP_SIZES = ["8/10", "9/12", "10/14", "12/16", "14/18"]

INVENTORY = {
    "Courier Poly": {"variants": ["White", "Silver", "Pink", "Yellow"], "sizes": CP_SIZES, "price": 15},
    "Printed Courier Poly": {"variants": ["White", "Silver"], "sizes": PCP_SIZES, "price": 18},
    "Invoice Courier Poly": {"variants": ["Standard"], "sizes": PCP_SIZES, "price": 20},
    "Die-Cut Box": {"variants": ["Brown", "White"], "sizes": ["Small", "Medium", "Large"], "price": 30},
    "Carton Box": {"variants": ["Local", "Korean"], "sizes": ["Small", "Medium", "Large"], "price": 35},
    "Cellophane Poly": {"variants": ["Transparent"], "sizes": ["Small", "Large"], "price": 10},
    "Bubble Wrap": {"variants": ["Premium"], "sizes": ["1m", "5m", "10m"], "price": 50},
    "Round Logo Sticker": {"variants": ["Standard"], "sizes": ['1"', '1.5"', '2"', '2.5"'], "price": 2}
}

# --- SERVER LOGIC ---
class AdminDashboardHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_POST(self):
        global BOT_ACTIVE, STATS, DELIVERY_CHARGE
        content_length = int(self.headers.get('Content-Length', 0))
        try:
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))
        except:
            self.send_response(400); self.end_headers(); return

        if hashlib.sha256(data.get('password', '').encode()).hexdigest() != DASHBOARD_PASSWORD_HASH:
            self.send_response(401); self.end_headers(); return

        res = {"status": "error"}
        if self.path == '/api/stats':
            uptime = f"{int((time.time()-BOT_START_TIME)//3600)}h {int(((time.time()-BOT_START_TIME)%3600)//60)}m"
            res = {"status": "online" if BOT_ACTIVE else "off", "uptime": uptime, "total_orders": STATS["total_orders"], "total_revenue": STATS["total_revenue"]}
        elif self.path == '/api/control':
            action = data.get('action')
            if action == 'start': BOT_ACTIVE = True
            elif action == 'stop': BOT_ACTIVE = False
            elif action == 'set_delivery': DELIVERY_CHARGE = int(data.get('value', 60))
            res = {"status": "success"}
        
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps(res).encode())

    def do_GET(self):
        self.path = '/index.html' if self.path == '/' else self.path
        return super().do_GET()

def run_server():
    with socketserver.TCPServer(("", PORT), AdminDashboardHandler) as httpd:
        httpd.allow_reuse_address = True
        print(f"🌐 Dashboard active on port {PORT}")
        httpd.serve_forever()

# --- BOT LOGIC ---
SELECT_PRODUCT, SELECT_VARIANT, SELECT_SIZE, SELECT_QUANTITY, CHECKOUT = range(5)

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = []
    items = list(INVENTORY.keys())
    for i in range(0, len(items), 2):
        row = [InlineKeyboardButton(items[i], callback_data=f"prod_{items[i]}")]
        if i+1 < len(items): row.append(InlineKeyboardButton(items[i+1], callback_data=f"prod_{items[i+1]}"))
        keyboard.append(row)
    
    text = "Details saved! Select a product:" if context.user_data.get('is_new', False) else "Select a product:"
    context.user_data['is_new'] = False
    
    if update.callback_query: await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_PRODUCT

async def select_variant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "back_to_prod": return await show_products(update, context)
    
    prod = query.data.replace("prod_", "") if "prod_" in query.data else context.user_data.get('current', {}).get('product', '')
    context.user_data['current'] = {'product': prod}
    
    keyboard = [[InlineKeyboardButton(v, callback_data=f"var_{v}")] for v in INVENTORY[prod]['variants']]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_prod")])
    await query.edit_message_text(f"Product: {prod}\nSelect variation:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_VARIANT

async def select_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "back_to_var": return await select_variant(update, context)
    
    var = query.data.replace("var_", "") if "var_" in query.data else context.user_data['current'].get('variant', '')
    context.user_data['current']['variant'] = var
    prod = context.user_data['current']['product']
    
    keyboard = []
    sizes = INVENTORY[prod]['sizes']
    for i in range(0, len(sizes), 3):
        row = [InlineKeyboardButton(s, callback_data=f"size_{s}") for s in sizes[i:i+3]]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_var")])
    await query.edit_message_text(f"Selected: {prod} ({var})\nChoose Size:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SIZE

async def select_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "back_to_size": return await select_size(update, context)
    
    context.user_data['current']['size'] = query.data.replace("size_", "")
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="back_to_size")]]
    await query.edit_message_text("✏️ Type the Quantity (e.g. 100):", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_QUANTITY

async def process_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    qty_text = update.message.text
    if not qty_text.isdigit():
        await update.message.reply_text("Please enter a valid number:")
        return SELECT_QUANTITY
    
    item = context.user_data.get('current', {})
    item['qty'] = int(qty_text)
    item['total'] = item['qty'] * INVENTORY[item['product']]['price']
    
    if 'cart' not in context.user_data: context.user_data['cart'] = []
    context.user_data['cart'].append(item)
    
    keyboard = [[InlineKeyboardButton("🛒 Add More", callback_data="add_more")], [InlineKeyboardButton("✅ Finish", callback_data="finish")]]
    await update.message.reply_text(f"Added {qty_text} units! What next?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT

async def generate_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "add_more": return await show_products(update, context)
    
    cart = context.user_data.get('cart', [])
    if not cart:
        await query.edit_message_text("Cart is empty. Please start again.")
        return ConversationHandler.END

    raw_info = context.user_data.get('customer_info', 'N/A')
    clean_info = re.sub(r"(অর্ডার কনফার্ম করার জন্য আমাদেরকে নিচের তথ্যগুলো দিন|To Confirm Order, Give us your)", "", raw_info, flags=re.IGNORECASE).strip()
    
    inv = f"📦 Pack & Wrap Invoice\n{'-'*25}\nCustomer:\n{clean_info}\n{'-'*25}\nItems:\n"
    subtotal = sum(i['total'] for i in cart)
    for i, item in enumerate(cart, 1):
        inv += f"{i}. {item['product']} ({item['variant']} {item['size']}) x{item['qty']} = {item['total']} BDT\n"
    
    total = subtotal + DELIVERY_CHARGE
    inv += f"{'-'*25}\nSubtotal: {subtotal} BDT\nDelivery: {DELIVERY_CHARGE} BDT\nTotal: {total} BDT\n{'-'*25}\nThank you!"
    
    global STATS; STATS["total_orders"] += 1; STATS["total_revenue"] += total
    await query.edit_message_text(f"✅ Order Done! Tap to copy:\n\n<code>{inv}</code>", parse_mode='HTML')
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_ACTIVE: return
    context.user_data['cart'] = []; context.user_data['is_new'] = True
    context.user_data['customer_info'] = update.message.text
    return await show_products(update, context)

def main():
    threading.Thread(target=run_server, daemon=True).start()
    
    # 1. Initialize Persistence
    persistence = PicklePersistence(filepath="bot_data.pickle")

    app = ApplicationBuilder().token('8615265508:AAG05nLqzYyI8qe6nZkfAolSiU56RZRLAR4').persistence(persistence).build()
    
    order_trigger = re.compile(r"(Name:|নাম:)", re.IGNORECASE)
    conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(order_trigger), start), 
            CommandHandler('start', start)
        ],
        states={
            SELECT_PRODUCT: [CallbackQueryHandler(select_variant, pattern='^prod_')],
            SELECT_VARIANT: [
                CallbackQueryHandler(select_size, pattern='^var_'), 
                CallbackQueryHandler(show_products, pattern='^back_to_prod$')
            ],
            SELECT_SIZE: [
                CallbackQueryHandler(select_qty, pattern='^size_'), 
                CallbackQueryHandler(select_variant, pattern='^back_to_var$')
            ],
            SELECT_QUANTITY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_qty), 
                CallbackQueryHandler(select_size, pattern='^back_to_size$')
            ],
            CHECKOUT: [
                CallbackQueryHandler(generate_invoice, pattern='^finish$'), 
                CallbackQueryHandler(show_products, pattern='^add_more$')
            ]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: ConversationHandler.END)],
        allow_reentry=True,  # Crucial: allows starting a new order if stuck
        name="pack_wrap_conversation",
        persistent=True
    )
    app.add_handler(conv); app.run_polling()

if __name__ == '__main__': main()