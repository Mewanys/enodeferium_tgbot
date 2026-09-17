import html
import secrets
import string
import time
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from keyboards import *
from xui import XUIError

router = Router()


class UserState(StatesGroup):
    waiting_receipt = State()


def fmt_date(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')


def is_admin(c, uid):
    return uid in c.admin_ids


def clean_newlines(text):
    # .env stores literal \\n, which must become real line breaks for Telegram.
    return (text or '').replace('\\n', '\n').replace('\\r', '\r')


async def home_text():
    return '👋 <b>Enferium</b>\n\nВыберите действие:'


async def sync_user_subscriptions(db, xui, user_id):
    """Synchronize every local subscription against 3x-ui.

    3x-ui is authoritative. A failed check is fail-closed: the subscription is
    hidden from the user until a later successful check confirms the client
    exists again. This prevents stale local records from being shown when the
    panel cannot be verified.
    """
    rows = await db.all_subscriptions_for_user(user_id)
    rows = [r for r in rows if not r['deleted']]
    changed = False
    for row in rows:
        try:
            exists, xui_enabled, xui_expiry = await xui.get_client_state(row['xui_email'])
            if not exists or not xui_enabled:
                if row['enabled']:
                    await db.disable_subscription(row['id'])
                    changed = True
                continue

            # Successful verification: restore a locally hidden subscription if
            # the client exists again, and keep the panel expiry authoritative.
            expiry = xui_expiry if xui_expiry > 0 else row['expiry_ms']
            await db.update_subscription(
                row['id'], expiry, 1, row['warned_3d']
            )
            if not row['enabled'] or (xui_expiry > 0 and xui_expiry != row['expiry_ms']):
                changed = True
        except Exception as e:
            # Fail closed as requested: if the panel cannot be verified, do not
            # continue showing the stale subscription to the user.
            await db.disable_subscription(row['id'])
            changed = True
            print(f'[SUB CHECK] #{row["id"]}: verification failed -> hidden: {e}')
    return changed


async def show_tariffs(target, db, config, user_id):
    rows = await db.tariffs()
    text = '💳 <b>Тарифы</b>\n\nВыберите тариф:'
    for r in rows:
        text += f"\n\n<b>{html.escape(r['name'])}</b> — {html.escape(r['price'])}\n{r['days']} дней. {html.escape(r['description'])}"
    await target.edit_text(text, reply_markup=tariffs_kb(rows), parse_mode='HTML')


async def show_subscriptions(target, db, xui, user_id):
    await sync_user_subscriptions(db, xui, user_id)
    rows = await db.subscriptions_for_user(user_id)
    if not rows:
        await target.edit_text('📱 У вас пока нет активных подписок.', reply_markup=back_home())
        return
    await target.edit_text(
        '📱 <b>Мои подписки</b>\n\nВыберите подписку:',
        reply_markup=subs_kb(rows),
        parse_mode='HTML'
    )


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, db, config):
    await state.clear()
    await db.upsert_user(m.from_user)
    await m.answer(
        await home_text(),
        reply_markup=main_reply_kb(is_admin(config, m.from_user.id)),
        parse_mode='HTML'
    )


# Persistent bottom keyboard buttons. No /start is required anymore.
@router.message(F.text == '💳 Тарифы')
async def menu_tariffs(m, db, config):
    await db.upsert_user(m.from_user)
    rows = await db.tariffs()
    text = '💳 <b>Тарифы</b>\n\nВыберите тариф:'
    for r in rows:
        text += f"\n\n<b>{html.escape(r['name'])}</b> — {html.escape(r['price'])}\n{r['days']} дней. {html.escape(r['description'])}"
    await m.answer(text, reply_markup=tariffs_kb(rows), parse_mode='HTML')


@router.message(F.text == '📱 Мои подписки')
async def menu_subscriptions(m, db, xui):
    await db.upsert_user(m.from_user)
    await sync_user_subscriptions(db, xui, m.from_user.id)
    rows = await db.subscriptions_for_user(m.from_user.id)
    if not rows:
        return await m.answer('📱 У вас пока нет активных подписок.')
    await m.answer('📱 <b>Мои подписки</b>\n\nВыберите подписку:', reply_markup=subs_kb(rows), parse_mode='HTML')


@router.message(F.text == '⚙️ Админ-панель')
async def menu_admin(m, config):
    if not is_admin(config, m.from_user.id):
        return
    await m.answer('⚙️ <b>Админ-панель</b>\n\nВыберите действие:', reply_markup=admin_kb(), parse_mode='HTML')


@router.message(F.text == '❌ Отмена оплаты')
async def cancel_payment_message(m, state, db, config):
    await state.clear()
    await m.answer(
        await home_text(),
        reply_markup=main_reply_kb(is_admin(config, m.from_user.id)),
        parse_mode='HTML'
    )


@router.callback_query(F.data == 'home')
async def home(q: CallbackQuery, state: FSMContext, db, config):
    await state.clear()
    await q.message.edit_text(
        await home_text(),
        reply_markup=main_kb(is_admin(config, q.from_user.id)),
        parse_mode='HTML'
    )
    await q.answer()


@router.callback_query(F.data == 'tariffs')
async def tariffs(q, db, config):
    rows = await db.tariffs()
    text = '💳 <b>Тарифы</b>\n\nВыберите тариф:'
    for r in rows:
        text += f"\n\n<b>{html.escape(r['name'])}</b> — {html.escape(r['price'])}\n{r['days']} дней. {html.escape(r['description'])}"
    await q.message.edit_text(text, reply_markup=tariffs_kb(rows), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('tariff:'))
async def tariff_selected(q, state, db, config):
    tid = int(q.data.split(':')[1])
    r = await db.tariff(tid)
    if not r or not r['enabled']:
        return await q.answer('Тариф недоступен', show_alert=True)

    # IMPORTANT: every purchase gets its own payment, UUID and subId.
    # Existing subscriptions are never extended by a new purchase.
    pid = await db.create_payment(q.from_user.id, tid, None, r['price'])
    details = clean_newlines(await db.setting('payment_details', config.payment_details))
    text = (
        f"💳 <b>{html.escape(r['name'])}</b>\n"
        f"Цена: <b>{html.escape(r['price'])}</b>\n"
        f"Срок: <b>{r['days']} дней</b>\n\n"
        f"<b>Оплата:</b>\n{html.escape(details)}\n\n"
        "После оплаты нажмите кнопку ниже и отправьте фото/файл чека."
    )
    await state.update_data(payment_id=pid)
    await q.message.edit_text(text, reply_markup=pay_kb(pid), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('waitreceipt:'))
async def wait_receipt(q, state, config):
    pid = int(q.data.split(':')[1])
    await state.set_state(UserState.waiting_receipt)
    await state.update_data(payment_id=pid)
    await q.message.answer(
        '📎 Отправьте сюда чек (фото или документ).\n\n'
        'Если передумали, нажмите «❌ Отмена оплаты».',
        reply_markup=cancel_payment_kb()
    )
    await q.answer()


async def handle_receipt(m, state, db, config, bot, file_id, file_type, caption=''):
    data = await state.get_data()
    pid = data.get('payment_id')
    if not pid:
        return
    p = await db.payment(pid)
    if not p or p['status'] != 'pending':
        await state.clear()
        return await m.answer('Этот платёж уже обработан.')
    await db.attach_receipt(pid, file_id, file_type)
    p = await db.payment(pid)
    await notify_admins_receipt(bot, config, p, caption)
    await state.clear()
    await m.answer(
        '✅ Чек отправлен администратору на проверку. Ожидайте решения.',
        reply_markup=main_reply_kb(is_admin(config, m.from_user.id))
    )


@router.message(UserState.waiting_receipt, F.photo)
async def receipt_photo(m, state, db, config, bot):
    await handle_receipt(m, state, db, config, bot, m.photo[-1].file_id, 'photo', m.caption or '')


@router.message(UserState.waiting_receipt, F.document)
async def receipt_doc(m, state, db, config, bot):
    await handle_receipt(m, state, db, config, bot, m.document.file_id, 'document', m.caption or '')


async def notify_admins_receipt(bot, config, p, caption):
    text = (
        f"🧾 <b>Новая оплата #{p['id']}</b>\n\n"
        f"Пользователь: {html.escape(p['first_name'])} (@{html.escape(p['username'] or 'нет')})\n"
        f"ID: <code>{p['user_id']}</code>\n"
        f"Тариф: {html.escape(p['tariff_name'])}\n"
        f"Цена: {html.escape(p['price'])}\n"
        f"Дней: {p['days']}\n\n"
        f"Комментарий: {html.escape(caption or '—')}"
    )
    for aid in config.admin_ids:
        try:
            if p['telegram_file_type'] == 'photo':
                await bot.send_photo(aid, p['telegram_file_id'], caption=text, parse_mode='HTML', reply_markup=receipt_admin_kb(p['id']))
            else:
                await bot.send_document(aid, p['telegram_file_id'], caption=text, parse_mode='HTML', reply_markup=receipt_admin_kb(p['id']))
        except Exception as e:
            print(f'[ADMIN RECEIPT] {aid}: {e}')


@router.callback_query(F.data == 'subs')
async def subscriptions(q, db, xui):
    try:
        await sync_user_subscriptions(db, xui, q.from_user.id)
        rows = await db.subscriptions_for_user(q.from_user.id)
    except Exception as e:
        return await q.answer('Не удалось проверить подписки', show_alert=True)
    if not rows:
        return await q.message.edit_text('📱 У вас пока нет активных подписок.', reply_markup=back_home())
    await q.message.edit_text('📱 <b>Мои подписки</b>\n\nВыберите подписку:', reply_markup=subs_kb(rows), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('sub:'))
async def sub_detail(q, db, config, xui):
    sid = int(q.data.split(':')[1])
    r = await db.subscription_by_user(q.from_user.id, sid)
    if not r:
        return await q.answer('Подписка не найдена', show_alert=True)

    try:
        exists, enabled, expiry = await xui.get_client_state(r['xui_email'])
        if not exists or not enabled:
            await db.disable_subscription(r['id'])
            return await q.answer('Подписка больше не существует в 3x-ui', show_alert=True)
        if expiry > 0:
            await db.update_subscription(r['id'], expiry, 1, r['warned_3d'])
            r = await db.subscription_by_user(q.from_user.id, sid)
    except Exception as e:
        await db.disable_subscription(r['id'])
        print(f'[SUB DETAIL] #{sid}: verification failed -> hidden: {e}')
        return await q.answer('Не удалось проверить подписку. Она временно скрыта.', show_alert=True)

    url = await db.setting('subscription_url_template', config.subscription_url_template)
    url = url.replace('{sub_id}', r['sub_id'])
    status = 'активна' if r['expiry_ms'] > int(time.time() * 1000) else 'истекла'
    text = (
        f"📱 <b>Подписка №{r['id']} от {datetime.fromisoformat(r['created_at']).strftime('%d.%m.%Y')}</b>\n\n"
        f"📧 Email: <code>{html.escape(r['xui_email'])}</code>\n"
        f"Статус: <b>{status}</b>\n"
        f"До: <b>{fmt_date(r['expiry_ms'])}</b>\n\n"
        f"🔗 <code>{html.escape(url)}</code>\n\n"
        "Скопируйте ссылку и добавьте её в VPN-клиент."
    )
    kb = sub_copy_kb(r['id'])
    await q.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('renew:'))
async def renew_subscription(q, db, xui):
    sid = int(q.data.split(':')[1])
    r = await db.subscription_by_user(q.from_user.id, sid)
    if not r or not r['enabled']:
        return await q.answer('Подписка недоступна', show_alert=True)

    try:
        exists, enabled, expiry = await xui.get_client_state(r['xui_email'])
        if not exists or not enabled:
            await db.disable_subscription(r['id'])
            return await q.answer('Подписка больше не существует в 3x-ui', show_alert=True)
        if expiry > 0:
            await db.update_subscription(r['id'], expiry, 1, r['warned_3d'])
    except Exception as e:
        await db.disable_subscription(r['id'])
        print(f'[RENEW CHECK] #{sid}: {e}')
        return await q.answer('Не удалось проверить подписку. Она временно скрыта.', show_alert=True)

    rows = await db.tariffs()
    if not rows:
        return await q.answer('Нет доступных тарифов', show_alert=True)

    text = '🔄 <b>Продление подписки</b>\n\nВыберите срок продления:'
    await q.message.edit_text(text, reply_markup=renew_tariffs_kb(rows, sid), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('renewtariff:'))
async def renew_tariff_selected(q, state, db, config):
    _, sid_s, tid_s = q.data.split(':')
    sid, tid = int(sid_s), int(tid_s)
    sub = await db.subscription_by_user(q.from_user.id, sid)
    tariff = await db.tariff(tid)
    if not sub or not sub['enabled']:
        return await q.answer('Подписка недоступна', show_alert=True)
    if not tariff or not tariff['enabled']:
        return await q.answer('Тариф недоступен', show_alert=True)

    pid = await db.create_payment(q.from_user.id, tid, sid, tariff['price'])
    details = clean_newlines(await db.setting('payment_details', config.payment_details))
    text = (
        f"🔄 <b>Продление подписки</b>\n\n"
        f"Тариф: <b>{html.escape(tariff['name'])}</b>\n"
        f"Цена: <b>{html.escape(tariff['price'])}</b>\n"
        f"Продление: <b>{tariff['days']} дней</b>\n\n"
        f"<b>Оплата:</b>\n{html.escape(details)}\n\n"
        "После оплаты нажмите кнопку ниже и отправьте чек."
    )
    await state.update_data(payment_id=pid)
    await q.message.edit_text(text, reply_markup=pay_kb(pid), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('del_sub:'))
async def delete_subscription(q, db, xui):
    sid = int(q.data.split(':')[1])
    r = await db.subscription_by_user(q.from_user.id, sid)
    if not r:
        return await q.answer('Подписка не найдена', show_alert=True)

    try:
        exists, _, _ = await xui.get_client_state(r['xui_email'])
        if exists:
            await xui.delete_client(r['xui_email'])
        await db.delete_subscription(r['id'])
    except Exception as e:
        print(f'[SUB DELETE] #{sid}: {e}')
        return await q.answer('Не удалось удалить подписку из 3x-ui', show_alert=True)

    await q.message.edit_text(
        '🗑 <b>Подписка удалена.</b>',
        reply_markup=back_home(),
        parse_mode='HTML'
    )
    await q.answer('Подписка удалена')
