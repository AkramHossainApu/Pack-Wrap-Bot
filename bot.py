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
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SYSTEM STATE & SECURITY ---
_secret = "".join(chr(c) for c in (48, 49, 50, 55))
DASHBOARD_PASSWORD_HASH = hashlib.sha256(_secret.encode()).hexdigest()
del _secret

BOT_ACTIVE = True 
BOT_START_TIME = time.time()
STATS = {
    "total_orders": 0,
    "total_revenue": 0
}

PORT = int(os.environ.get("PORT", 10000))

def verify_password(attempt):
    if not attempt: return False
    return hashlib.sha256(str(attempt).encode()).hexdigest() == DASHBOARD_PASSWORD_HASH

# --- VANILLA PYTHON WEB SERVER ---
class AdminDashboardHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = http.server.SimpleHTTPRequestHandler.extensions_map.copy()
    extensions_map.update({'.js': 'application/javascript', '.css': 'text/css'})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_POST(self):
        global BOT_ACTIVE, STATS, BOT_START_TIME
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            return self.send_json({"error": "Invalid JSON"}, 400)

        if not verify_password(data.get('password')):
            return self.send_json({"error": "Unauthorized"}, 401)

        if self.path == '/api/login':
            return self.send_json({"status": "success"})

        elif self.path == '/api/stats':
            uptime_seconds = int(time.time() - BOT_START_TIME)
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            return self.send_json({
                "status": "online" if BOT_ACTIVE else "maintenance",
                "uptime": f"{hours}h {minutes}m",
                "total_orders": STATS["total_orders"],
                "total_revenue": STATS["total_revenue"]
            })

        elif self.path == '/api/control':
            action = data.get('action')
            if action == 'start': BOT_ACTIVE = True
            elif action == 'stop': BOT_ACTIVE = False
            elif action == 'restart':
                BOT_ACTIVE = True
                STATS = {"total_orders": 0, "total_revenue": 0}
                BOT_START_TIME = time.time()
            return self.send_json({"status": "success", "state": "online" if BOT_ACTIVE else "maintenance"})

        self.send_json({"error": "Not found"}, 404)

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) and '.' not in self.path:
            self.path = '/index.html'
        return super().do_GET()

class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def run_server():
    with ReuseTCPServer(("", PORT), AdminDashboardHandler) as httpd:
        print(f"🌐 Admin Dashboard running on port {PORT}")
        httpd.serve_forever()


# --- TELEGRAM BOT LOGIC ---
SELECT_PRODUCT, SELECT_VARIANT, SELECT_SIZE, SELECT_QUANTITY, CHECKOUT = range(5)

INVENTORY = {
    "Courier Poly": { "variants": ["White", "Silver", "Printed"], "sizes": ["10x14", "12x16", "15x20"], "price": 15 },
    "Bubble Wrap": { "variants": ["Premium", "Standard"], "sizes": ["1 Meter", "5 Meter", "10 Meter"], "price": 50 },
    "Cellophane Poly": { "variants": ["Transparent"], "sizes": ["Small", "Large"], "price": 10 },
    "Boxes": { "variants": ["Cartoon", "Die-Cut"], "sizes": ["Small", "Medium", "Large"], "price": 30 }
}

async def check_bot_status(update: Update) -> bool:
    if not BOT_ACTIVE:
        msg = "⚙️ Our ordering system is currently offline for maintenance."
        if update.message: await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer("System Offline", show_alert=True)
            await update.callback_query.message.edit_text(msg)
        return False
    return True

async def process_initial_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    context.user_data['cart'] = [] 
    context.user_data['customer_info'] = update.message.text
    context.user_data['is_new_order'] = True # Flag to show the combined text
    return await show_products(update, context)

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton(p, callback_data=f"prod_{p}")] for p in INVENTORY.keys()]
    
    # Check if this is the very first step to combine the messages
    if context.user_data.get('is_new_order'):
        text = "Details saved! Let's build your order.\nPlease select a product:"
        context.user_data['is_new_order'] = False # Reset the flag
    else:
        text = "Please select a product:"

    if update.callback_query: 
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else: 
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_PRODUCT 

async def select_variant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    product = query.data.replace("prod_", "")
    context.user_data['current_item'] = {'product': product}
    keyboard = [[InlineKeyboardButton(v, callback_data=f"var_{v}")] for v in INVENTORY[product]['variants']]
    await query.edit_message_text(f"Selected: {product}\nSelect color/type:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_VARIANT 

async def select_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data['current_item']['variant'] = query.data.replace("var_", "")
    product = context.user_data['current_item']['product']
    keyboard = [[InlineKeyboardButton(s, callback_data=f"size_{s}")] for s in INVENTORY[product]['sizes']]
    await query.edit_message_text("Great! Now select a size:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SIZE 

async def select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data['current_item']['size'] = query.data.replace("size_", "")
    
    # We remove the keyboard and ask them to type the number instead
    await query.edit_message_text("✏️ Please type the exact quantity you need (e.g., 150):")
    return SELECT_QUANTITY 

async def process_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Catches the manually typed quantity, calculates subtotal, and proceeds to checkout."""
    if not await check_bot_status(update): return ConversationHandler.END
    
    user_input = update.message.text.strip()
    
    # Make sure they actually typed a number
    if not user_input.isdigit():
        await update.message.reply_text("❌ Please enter a valid number for the quantity:")
        return SELECT_QUANTITY

    qty = int(user_input)
    context.user_data['current_item']['qty'] = qty
    product = context.user_data['current_item']['product']
    context.user_data['current_item']['subtotal'] = INVENTORY[product]['price'] * qty
    context.user_data['cart'].append(context.user_data['current_item'])
    
    keyboard = [[InlineKeyboardButton("🛒 Add Another Item", callback_data="add_more")], [InlineKeyboardButton("✅ Generate Invoice", callback_data="finish_order")]]
    await update.message.reply_text(f"✅ {qty} units added to your cart! What next?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT

async def generate_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    if query.data == "add_more": return await show_products(update, context)
        
    cart = context.user_data.get('cart', [])
    
    # Building a clean, text-only invoice string
    invoice_text = "📦 Pack & Wrap - Final Invoice 📦\n"
    invoice_text += "---------------------------------\n"
    invoice_text += f"Customer Details:\n{context.user_data.get('customer_info', '')}\n"
    invoice_text += "---------------------------------\n"
    invoice_text += "Order Summary:\n"
    
    total = 0
    for idx, item in enumerate(cart, 1):
        invoice_text += f"{idx}. {item['product']} ({item['variant']}, {item['size']}) x {item['qty']} = {item['subtotal']} BDT\n"
        total += item['subtotal']
        
    invoice_text += "---------------------------------\n"
    invoice_text += f"Total Due: {total} BDT\n"
    invoice_text += "Delivery: Cash on Delivery\n"
    invoice_text += "Thank you!"
    
    if total > 0:
        global STATS
        STATS["total_orders"] += 1
        STATS["total_revenue"] += total
    
    # We wrap the invoice inside an HTML <code> tag to make it tap-to-copy
    final_message = f"✅ Order complete! Tap the text box below to instantly copy your invoice:\n\n<code>{invoice_text}</code>"
    
    await query.edit_message_text(final_message, parse_mode='HTML')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Order cancelled.")
    return ConversationHandler.END

def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token('8615265508:AAG05nLqzYyI8qe6nZkfAolSiU56RZRLAR4').build()
    order_trigger = re.compile(r"(Name:|নাম:)", re.IGNORECASE)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(order_trigger), process_initial_order)],
        states={
            SELECT_PRODUCT: [CallbackQueryHandler(select_variant, pattern='^prod_')],
            SELECT_VARIANT: [CallbackQueryHandler(select_size, pattern='^var_')],
            SELECT_SIZE: [CallbackQueryHandler(select_quantity, pattern='^size_')],
            # Notice this changed to a MessageHandler to catch the typed numbers
            SELECT_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_quantity_input)],
            CHECKOUT: [
                CallbackQueryHandler(generate_invoice, pattern='^(add_more|finish_order)$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("🤖 Telegram Bot is listening for orders...")
    app.run_polling()

if __name__ == '__main__':
    main()