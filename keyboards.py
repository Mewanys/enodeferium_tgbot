from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime


def main_kb(admin=False):
    """Inline keyboard used inside messages."""
    b = InlineKeyboardBuilder()
    b.button(text='💳 Тарифы', callback_data='tariffs')
    b.button(text='📱 Мои подписки', callback_data='subs')
    b.adjust(2)
    if admin:
        b.button(text='⚙️ Админ-панель', callback_data='admin')
    return b.as_markup()


def main_reply_kb(admin=False):
    """Persistent keyboard at the bottom of the Telegram chat."""
    rows = [
        [KeyboardButton(text='💳 Тарифы'), KeyboardButton(text='📱 Мои подписки')],
    ]
    if admin:
        rows.append([KeyboardButton(text='⚙️ Админ-панель')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def cancel_payment_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='❌ Отмена оплаты')]],
        resize_keyboard=True,
        is_persistent=True,
    )


def back_home():
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='◀️ В меню', callback_data='home')]]
    )


def tariffs_kb(rows):
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"{r['name']} — {r['price']}", callback_data=f"tariff:{r['id']}")
    b.button(text='◀️ Назад', callback_data='home')
    b.adjust(1)
    return b.as_markup()


def subs_kb(rows):
    b = InlineKeyboardBuilder()
    for r in rows:
        status = '🟢' if r['enabled'] else '🔴'
        try:
            dt = r['created_at'].replace('T', ' ')[:16]
            created = datetime.fromisoformat(r['created_at']).strftime('%d.%m.%Y')
        except Exception:
            created = str(r['created_at'])[:10]
        b.button(text=f"{status} Подписка №{r['id']} от {created}", callback_data=f"sub:{r['id']}")
    b.button(text='◀️ Назад', callback_data='home')
    b.adjust(1)
    return b.as_markup()


def renew_tariffs_kb(rows, sid):
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"{r['name']} — {r['price']}", callback_data=f"renewtariff:{sid}:{r['id']}")
    b.button(text='◀️ Назад', callback_data=f'sub:{sid}')
    b.adjust(1)
    return b.as_markup()


def pay_kb(pid):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📎 Отправить чек', callback_data=f'waitreceipt:{pid}')],
            [InlineKeyboardButton(text='◀️ Назад', callback_data='tariffs')],
        ]
    )


def receipt_admin_kb(pid):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='✅ Одобрить', callback_data=f'approve:{pid}'),
                InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject:{pid}'),
            ]
        ]
    )


def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text='💰 Платежи', callback_data='admin:payments')
    b.button(text='📦 Тарифы', callback_data='admin:tariffs')
    b.button(text='📱 Подписки', callback_data='admin:subs')
    b.button(text='📢 Оповещение', callback_data='admin:notify')
    b.button(text='⚙️ Настройки', callback_data='admin:settings')
    b.button(text='◀️ В меню', callback_data='home')
    b.adjust(2, 2, 2)
    return b.as_markup()


def admin_tariffs_kb(rows):
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"{'🟢' if r['enabled'] else '🔴'} {r['name']}", callback_data=f"admintariff:{r['id']}")
    b.button(text='➕ Добавить тариф', callback_data='tariff:add')
    b.button(text='◀️ Админка', callback_data='admin')
    b.adjust(1)
    return b.as_markup()


def admin_payment_kb(pid):
    return receipt_admin_kb(pid)


def settings_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='💳 Реквизиты', callback_data='set:payment')],
            [InlineKeyboardButton(text='🔗 URL подписки', callback_data='set:suburl')],
            [InlineKeyboardButton(text='◀️ Админка', callback_data='admin')],
        ]
    )


def admin_pending_payments_kb(rows):
    b = InlineKeyboardBuilder()
    for r in rows:
        kind = '🔄' if r['subscription_id'] else '🆕'
        buyer = r['first_name'] or r['username'] or str(r['user_id'])
        b.button(text=f"{kind} #{r['id']} • {buyer} • {r['tariff_name']} • {r['price']}", callback_data=f"admin:payment:{r['id']}")
    b.button(text='◀️ Админка', callback_data='admin')
    b.adjust(1)
    return b.as_markup()


def admin_payment_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='◀️ Все платежи', callback_data='admin:payments')],
        [InlineKeyboardButton(text='⚙️ Админка', callback_data='admin')],
    ])


def admin_users_kb(rows):
    b = InlineKeyboardBuilder()
    for r in rows:
        name = r['first_name'] or (f"@{r['username']}" if r['username'] else str(r['user_id']))
        count = r['sub_count']
        b.button(text=f"👤 {name} • {count} подпис.", callback_data=f"admin:user:{r['user_id']}")
    b.button(text='🔄 Обновить', callback_data='admin:subs')
    b.button(text='◀️ Админка', callback_data='admin')
    b.adjust(1)
    return b.as_markup()


def admin_user_subs_kb(rows, user_id):
    b = InlineKeyboardBuilder()
    for r in rows:
        status = '🟢' if r['enabled'] else '⚪'
        b.button(text=f"{status} Подписка №{r['id']}", callback_data=f"admin:sub:{r['id']}")
    b.button(text='◀️ Пользователи', callback_data='admin:subs')
    b.adjust(1)
    return b.as_markup()


def admin_sub_detail_kb(sid, enabled=True):
    toggle = '⛔ Отключить' if enabled else '✅ Включить'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🗑 Удалить', callback_data=f'admin:subdelete:{sid}')],
        [InlineKeyboardButton(text=toggle, callback_data=f'admin:subtoggle:{sid}')],
        [InlineKeyboardButton(text='◀️ Назад', callback_data='admin:subs')],
    ])



def notification_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='◀️ Отмена', callback_data='admin')]
    ])

def sub_copy_kb(sid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 Продлить', callback_data=f'renew:{sid}')],
        [InlineKeyboardButton(text='🗑 Удалить', callback_data=f'del_sub:{sid}')],
        [InlineKeyboardButton(text='◀️ Назад', callback_data='subs')],
    ])
