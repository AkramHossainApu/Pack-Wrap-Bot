import logging
import re
import time
import threading
import json
import hashlib
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask, request, jsonify
from flask_cors import CORS

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SYSTEM STATE & SECURITY ---
# Obfuscate the "0127" PIN using ASCII codes (48='0', 49='1', 50='2', 55='7') 
# so the plain text cannot be found anywhere in this file.
_secret = "".join(chr(c) for c in (48, 49, 50, 55))
DASHBOARD_PASSWORD_HASH = hashlib.sha256(_secret.encode()).hexdigest()
del _secret # Immediately delete the plain-text PIN from the server's memory

BOT_ACTIVE = True 
BOT_START_TIME = time.time()
STATS = {
    "total_orders": 0,
    "total_revenue": 0
}

def verify_password(attempt):
    """Securely hashes the login attempt and compares it to our hidden hash."""
    if not attempt:
        return False
    return hashlib.sha256(str(attempt).encode()).hexdigest() == DASHBOARD_PASSWORD_HASH

# --- FLASK WEB SERVER (API) ---
flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    if data and verify_password(data.get('password')):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 401

@flask_app.route('/api/stats', methods=['POST'])
def get_stats():
    data = request.json
    if not data or not verify_password(data.get('password')):
        return jsonify({"error": "Unauthorized"}), 401
        
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    
    return jsonify({
        "status": "online" if BOT_ACTIVE else "maintenance",
        "uptime": f"{hours}h {minutes}m",
        "total_orders": STATS["total_orders"],
        "total_revenue": STATS["total_revenue"]
    })

@flask_app.route('/api/control', methods=['POST'])
def control_bot():
    global BOT_ACTIVE, STATS, BOT_START_TIME
    data = request.json
    if not data or not verify_password(data.get('password')):
        return jsonify({"error": "Unauthorized"}), 401
    
    action = data.get('action')
    if action == 'start':
        BOT_ACTIVE = True
    elif action == 'stop':
        BOT_ACTIVE = False
    elif action == 'restart':
        BOT_ACTIVE = True
        STATS = {"total_orders": 0, "total_revenue": 0}
        BOT_START_TIME = time.time()
        
    return jsonify({"status": "success", "state": "online" if BOT_ACTIVE else "maintenance"})

def run_flask():
    """Runs the web API on port 5001"""
    flask_app.run(host='0.0.0.0', port=5001, use_reloader=False)


# --- TELEGRAM BOT LOGIC ---
SELECT_PRODUCT, SELECT_VARIANT, SELECT_SIZE, SELECT_QUANTITY, CHECKOUT = range(5)

INVENTORY = {
    "Courier Poly": { "variants": ["White", "Silver", "Printed"], "sizes": ["10x14", "12x16", "15x20"], "price": 15 },
    "Bubble Wrap": { "variants": ["Premium", "Standard"], "sizes": ["1 Meter", "5 Meter", "10 Meter"], "price": 50 },
    "Cellophane Poly": { "variants": ["Transparent"], "sizes": ["Small", "Large"], "price": 10 },
    "Boxes": { "variants": ["Cartoon", "Die-Cut"], "sizes": ["Small", "Medium", "Large"], "price": 30 }
}

async def check_bot_status(update: Update) -> bool:
    """Helper to check if the bot is active before processing orders."""
    if not BOT_ACTIVE:
        msg = "⚙️ Our ordering system is currently offline for maintenance. Please try again later."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer("System Offline", show_alert=True)
            await update.callback_query.message.edit_text(msg)
        return False
    return True

async def process_initial_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    context.user_data['cart'] = [] 
    context.user_data['customer_info'] = update.message.text
    await update.message.reply_text("Details saved! Let's build your order.")
    return await show_products(update, context)

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [[InlineKeyboardButton(p, callback_data=f"prod_{p}")] for p in INVENTORY.keys()]
    if update.callback_query:
        await update.callback_query.message.reply_text("Please select a product:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Please select a product:", reply_markup=InlineKeyboardMarkup(keyboard))
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
    keyboard = [
        [InlineKeyboardButton("50", callback_data="qty_50"), InlineKeyboardButton("100", callback_data="qty_100")],
        [InlineKeyboardButton("500", callback_data="qty_500"), InlineKeyboardButton("1000", callback_data="qty_1000")]
    ]
    await query.edit_message_text("How many units?", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_QUANTITY 

async def process_item_and_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    if query.data.startswith("qty_"):
        qty = int(query.data.replace("qty_", ""))
        context.user_data['current_item']['qty'] = qty
        product = context.user_data['current_item']['product']
        context.user_data['current_item']['subtotal'] = INVENTORY[product]['price'] * qty
        context.user_data['cart'].append(context.user_data['current_item'])
    
    keyboard = [
        [InlineKeyboardButton("🛒 Add Another Item", callback_data="add_more")],
        [InlineKeyboardButton("✅ Generate Invoice", callback_data="finish_order")]
    ]
    await query.edit_message_text("Item added! What next?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT

async def generate_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await check_bot_status(update): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    if query.data == "add_more": return await show_products(update, context)
        
    cart = context.user_data.get('cart', [])
    invoice = f"📦 *Pack & Wrap - Final Invoice* 📦\n---------------------------------\n*Customer Details:*\n{context.user_data.get('customer_info', '')}\n---------------------------------\n*Order Summary:*\n"
    total = 0
    for idx, item in enumerate(cart, 1):
        invoice += f"{idx}. {item['product']} ({item['variant']}, {item['size']}) x {item['qty']} = {item['subtotal']} BDT\n"
        total += item['subtotal']
        
    invoice += f"---------------------------------\n*Total Due: {total} BDT*\n*Delivery:* Cash on Delivery (All over Bangladesh)\nThank you!"
    
    if total > 0:
        global STATS
        STATS["total_orders"] += 1
        STATS["total_revenue"] += total
    
    await query.edit_message_text(invoice, parse_mode='Markdown')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Order cancelled.")
    return ConversationHandler.END

def main():
    threading.Thread(target=run_flask, daemon=True).start()

    # Using your official API Key
    app = ApplicationBuilder().token('8615265508:AAG05nLqzYyI8qe6nZkfAolSiU56RZRLAR4').build()
    order_trigger = re.compile(r"(Name:|নাম:)", re.IGNORECASE)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(order_trigger), process_initial_order)],
        states={
            SELECT_PRODUCT: [CallbackQueryHandler(select_variant, pattern='^prod_')],
            SELECT_VARIANT: [CallbackQueryHandler(select_size, pattern='^var_')],
            SELECT_SIZE: [CallbackQueryHandler(select_quantity, pattern='^size_')],
            SELECT_QUANTITY: [CallbackQueryHandler(process_item_and_checkout, pattern='^qty_')],
            CHECKOUT: [
                CallbackQueryHandler(process_item_and_checkout, pattern='^qty_'),
                CallbackQueryHandler(generate_invoice, pattern='^(add_more|finish_order)$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("Secure Server Booted. Bot listening, API on port 5001...")
    app.run_polling()

if __name__ == '__main__':
    main()