import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Define conversation states
SELECT_PRODUCT, SELECT_VARIANT, SELECT_SIZE, SELECT_QUANTITY, CHECKOUT = range(5)

# Pack & Wrap Custom Inventory
INVENTORY = {
    "Courier Poly": {
        "variants": ["White", "Silver", "White Printed"],
        "sizes": ["10x14", "12x16", "15x20"],
        "price": 15
    },
    "Bubble Wrap": {
        "variants": ["Premium", "Standard"],
        "sizes": ["1 Meter", "5 Meter", "10 Meter"],
        "price": 50
    },
    "Cellophane Poly": {
        "variants": ["Transparent"],
        "sizes": ["Small", "Large"],
        "price": 10
    },
    "Boxes": {
        "variants": ["Cartoon Box", "Die-Cut Box"],
        "sizes": ["Small", "Medium", "Large"],
        "price": 30
    }
}

async def process_initial_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Detects pasted customer details, saves them, and shows products."""
    # Initialize an empty cart for this user session
    context.user_data['cart'] = [] 
    
    # Save the text block they just pasted
    context.user_data['customer_info'] = update.message.text
    
    await update.message.reply_text("Details saved! Let's build your order.")
    return await show_products(update, context)

async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows the main product list."""
    keyboard = []
    for product in INVENTORY.keys():
        keyboard.append([InlineKeyboardButton(product, callback_data=f"prod_{product}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.message.reply_text("Please select a product:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Please select a product:", reply_markup=reply_markup)
        
    return SELECT_VARIANT

async def select_variant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows variations/colors/types for the selected product."""
    query = update.callback_query
    await query.answer()
    
    product = query.data.replace("prod_", "")
    context.user_data['current_item'] = {'product': product}
    
    variants = INVENTORY[product]['variants']
    keyboard = [[InlineKeyboardButton(var, callback_data=f"var_{var}")] for var in variants]
    
    await query.edit_message_text(f"Selected: {product}\nNow, select a variation/color:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SIZE

async def select_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows sizes for the selected product."""
    query = update.callback_query
    await query.answer()
    
    variant = query.data.replace("var_", "")
    context.user_data['current_item']['variant'] = variant
    product = context.user_data['current_item']['product']
    
    sizes = INVENTORY[product]['sizes']
    keyboard = [[InlineKeyboardButton(size, callback_data=f"size_{size}")] for size in sizes]
    
    await query.edit_message_text("Great! Now select a size:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_QUANTITY

async def select_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows quantity options."""
    query = update.callback_query
    await query.answer()
    
    size = query.data.replace("size_", "")
    context.user_data['current_item']['size'] = size
    
    keyboard = [
        [InlineKeyboardButton("50", callback_data="qty_50"), InlineKeyboardButton("100", callback_data="qty_100")],
        [InlineKeyboardButton("500", callback_data="qty_500"), InlineKeyboardButton("1000", callback_data="qty_1000")]
    ]
    
    await query.edit_message_text("How many units do you need?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT

async def process_item_and_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Adds item to cart and asks to checkout or add more."""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("qty_"):
        qty = int(query.data.replace("qty_", ""))
        context.user_data['current_item']['qty'] = qty
        
        # Calculate price
        product = context.user_data['current_item']['product']
        unit_price = INVENTORY[product]['price']
        context.user_data['current_item']['subtotal'] = unit_price * qty
        
        # Add to cart
        context.user_data['cart'].append(context.user_data['current_item'])
    
    keyboard = [
        [InlineKeyboardButton("🛒 Add Another Item", callback_data="add_more")],
        [InlineKeyboardButton("✅ Generate Invoice", callback_data="finish_order")]
    ]
    
    await query.edit_message_text("Item added to cart! What would you like to do next?", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHECKOUT

async def generate_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generates the final invoice message."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "add_more":
        return await show_products(update, context)
        
    cart = context.user_data.get('cart', [])
    customer_info = context.user_data.get('customer_info', 'No details provided.')
    
    invoice = "📦 *Pack & Wrap - Final Invoice* 📦\n"
    invoice += "---------------------------------\n"
    invoice += f"*Customer Details:*\n{customer_info}\n"
    invoice += "---------------------------------\n"
    invoice += "*Order Summary:*\n"
    
    total = 0
    for idx, item in enumerate(cart, 1):
        name = item['product']
        var = item['variant']
        size = item['size']
        qty = item['qty']
        sub = item['subtotal']
        total += sub
        invoice += f"{idx}. {name} ({var}, {size}) x {qty} = {sub} BDT\n"
        
    invoice += "---------------------------------\n"
    invoice += f"*Total Due: {total} BDT*\n"
    invoice += "*Delivery:* Cash on Delivery (All over Bangladesh)\n\n"
    invoice += "Thank you for your order! We will process it shortly."
    
    await query.edit_message_text(invoice, parse_mode='Markdown')
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Order cancelled. Paste your details to start a new order.")
    return ConversationHandler.END

def main():
    # REPLACE WITH YOUR ACTUAL TOKEN
    app = ApplicationBuilder().token('YOUR_TELEGRAM_BOT_TOKEN').build()

    # Regex looks for "Name:" or "নাম:" (case-insensitive) anywhere in the message
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
    print("Bot is listening for orders 24/7...")
    app.run_polling()

if __name__ == '__main__':
    main()