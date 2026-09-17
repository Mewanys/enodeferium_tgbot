import asyncio
import html
import time, secrets, string
from datetime import datetime, timezone, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from keyboards import *

router=Router()

def admin_only(config, uid): return uid in config.admin_ids

def fmt(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).strftime('%d.%m.%Y %H:%M UTC')

def tg_comment(first_name, username):
    return f'@{username}' if username else (first_name or 'Telegram user')

class AdminState(StatesGroup):
    add_tariff=State(); edit_tariff=State(); payment=State(); suburl=State(); notify=State()

@router.callback_query(F.data=='admin')
async def admin(q,config,state):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    await state.clear()
    await q.message.edit_text('⚙️ <b>Админ-панель</b>\n\nЗдесь можно управлять тарифами, платежами, подписками и настройками.',reply_markup=admin_kb(),parse_mode='HTML'); await q.answer()

@router.callback_query(F.data=='admin:payments')
async def payments(q, db, config):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    rows = await db.pending_payments()
    if not rows:
        await q.message.edit_text(
            '💰 <b>Платежи</b>\n\nНет платежей, ожидающих проверки.',
            reply_markup=admin_kb(), parse_mode='HTML'
        )
        return await q.answer()

    text = (
        '💰 <b>Платежи на проверке</b>\n\n'
        'Выберите заявку — каждая заявка открывается отдельно вместе со своим чеком.'
    )
    await q.message.edit_text(text, reply_markup=admin_pending_payments_kb(rows), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('admin:payment:'))
async def admin_payment(q, db, config, bot):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    pid = int(q.data.split(':')[2])
    p = await db.payment(pid)
    if not p:
        return await q.answer('Заявка не найдена', show_alert=True)

    kind = 'Продление' if p['subscription_id'] else 'Новая подписка'
    text = (
        f'🧾 <b>Заявка #{p["id"]}</b>\n\n'
        f'👤 Покупатель: <b>{html.escape(p["first_name"] or "—")}</b>\n'
        f'Username: @{html.escape(p["username"] or "нет")}\n'
        f'ID: <code>{p["user_id"]}</code>\n\n'
        f'📦 Тип: <b>{kind}</b>\n'
        f'Тариф: <b>{html.escape(p["tariff_name"])}</b>\n'
        f'Срок: <b>{p["days"]} дней</b>\n'
        f'Цена: <b>{html.escape(p["price"])}</b>\n'
        f'Статус: <b>{html.escape(p["status"])}</b>'
    )

    markup = receipt_admin_kb(pid) if p['status'] == 'pending' else admin_payment_back_kb()
    if p['telegram_file_id']:
        try:
            if p['telegram_file_type'] == 'photo':
                await bot.send_photo(q.from_user.id, p['telegram_file_id'], caption=text, parse_mode='HTML', reply_markup=markup)
            else:
                await bot.send_document(q.from_user.id, p['telegram_file_id'], caption=text, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            print(f'[ADMIN PAYMENT VIEW #{pid}] {e}')
            await q.message.answer(text, reply_markup=markup, parse_mode='HTML')
    else:
        await q.message.answer(text + '\n\n⚠️ Чек в базе не найден.', reply_markup=markup, parse_mode='HTML')

    await q.answer()


@router.callback_query(F.data.startswith('approve:'))
async def approve(q, db, config, xui, bot):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    pid = int(q.data.split(':')[1])
    p = await db.payment(pid)
    if not p or p['status'] != 'pending':
        return await q.answer('Платёж уже обработан', show_alert=True)

    now = int(time.time() * 1000)
    expiry = now + p['days'] * 86400000

    try:
        if p['subscription_id']:
            # Renewal: keep the same 3x-ui client and extend only the selected
            # subscription. A renewal never creates a second client.
            sub = await db.subscription(p['subscription_id'])
            if not sub or sub['user_id'] != p['user_id']:
                raise RuntimeError('Связанная подписка не найдена')

            exists, enabled, current_expiry = await xui.get_client_state(sub['xui_email'])
            if not exists or not enabled:
                await db.disable_subscription(sub['id'])
                raise RuntimeError('Подписка больше не существует или отключена в 3x-ui')

            base_expiry = max(current_expiry or 0, int(time.time() * 1000))
            new_expiry = base_expiry + p['days'] * 86400000

            full = await xui.get_client_by_email(sub['xui_email'])
            obj = full.get('obj') or {}
            client = obj.get('client', obj)
            payload = {
                'email': client.get('email', sub['xui_email']),
                'subId': client.get('subId', sub['sub_id']),
                'id': client.get('id') or client.get('uuid'),
                'password': client.get('password'),
                'auth': client.get('auth'),
                'flow': client.get('flow') or '',
                'security': client.get('security') or 'auto',
                'totalGB': client.get('totalGB') or 0,
                'expiryTime': new_expiry,
                'limitIp': client.get('limitIp') or 0,
                'tgId': int(client.get('tgId') or p['user_id']),
                'reset': int(client.get('reset') or 0),
                'comment': tg_comment(p['first_name'], p['username']),
                'enable': True,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            await xui.update_client(sub['xui_email'], payload)
            await db.update_subscription(sub['id'], new_expiry, 1, 0)
            await db.process_payment(pid, 'approved', q.from_user.id)

            url = await db.setting('subscription_url_template', config.subscription_url_template)
            url = url.replace('{sub_id}', sub['sub_id'])
            await bot.send_message(
                p['user_id'],
                f"✅ <b>Продление подтверждено!</b>\n\n"
                f"Тариф: {p['tariff_name']}\n"
                f"Доступ до: <b>{fmt(new_expiry)}</b>\n\n"
                f"🔗 Ваша ссылка подписки:\n<code>{html.escape(url)}</code>",
                parse_mode='HTML',
            )
        else:
            # New purchase: always create a completely new client/subscription.
            suffix = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
            email = f"tg{p['user_id']}_{suffix}"
            sub_id = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))

            await xui.create_client(
                email=email,
                tg_id=p['user_id'],
                sub_id=sub_id,
                expiry_ms=expiry,
                inbound_ids=config.xui_inbound_ids,
                comment=tg_comment(p['first_name'], p['username']),
            )

            sid = await db.add_subscription(
                p['user_id'], email, sub_id, config.xui_inbound_ids, expiry
            )
            sub = await db.subscription(sid)
            await db.process_payment(pid, 'approved', q.from_user.id)

            url = await db.setting('subscription_url_template', config.subscription_url_template)
            url = url.replace('{sub_id}', sub['sub_id'])
            await bot.send_message(
                p['user_id'],
                f"✅ <b>Оплата подтверждена!</b>\n\n"
                f"Тариф: {p['tariff_name']}\n"
                f"Доступ до: <b>{fmt(expiry)}</b>\n\n"
                f"🔗 Ваша ссылка подписки:\n<code>{html.escape(url)}</code>",
                parse_mode='HTML',
            )

        await q.message.edit_reply_markup(reply_markup=None)
        await q.answer('Оплата подтверждена')

    except Exception as e:
        print(f'[APPROVE #{pid}] {e}')
        await q.answer('Ошибка 3x-ui: ' + str(e)[:150], show_alert=True)

@router.callback_query(F.data.startswith('reject:'))
async def reject(q,db,config,bot):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    pid=int(q.data.split(':')[1]); p=await db.payment(pid)
    if not p or p['status']!='pending': return await q.answer('Платёж уже обработан',show_alert=True)
    await db.process_payment(pid,'rejected',q.from_user.id)
    await bot.send_message(p['user_id'],f"❌ <b>Оплата #{pid} отклонена.</b>\n\nЕсли вы считаете, что это ошибка, свяжитесь с администратором.",parse_mode='HTML')
    await q.message.edit_reply_markup(reply_markup=None); await q.answer('Отклонено')

@router.callback_query(F.data=='admin:notify')
async def notify_start(q, state, config):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)
    await state.set_state(AdminState.notify)
    await q.message.edit_text(
        '📢 <b>Оповещение</b>\n\n'
        'Отправьте следующим сообщением текст или фотографию с подписью.\n'
        'Сообщение будет отправлено всем пользователям, у которых есть активная подписка.',
        reply_markup=notification_cancel_kb(),
        parse_mode='HTML'
    )
    await q.answer()


async def _broadcast(bot, db, config, text=None, photo_id=None):
    user_ids = await db.active_subscriber_ids()
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            if photo_id:
                await bot.send_photo(
                    uid,
                    photo_id,
                    caption=html.escape(text or ''),
                    parse_mode='HTML' if text else None,
                )
            else:
                await bot.send_message(uid, html.escape(text or ''), parse_mode='HTML')
            sent += 1
            await asyncio.sleep(0.04)
        except Exception as e:
            failed += 1
            print(f'[BROADCAST] {uid}: {e}')
    return len(user_ids), sent, failed


@router.message(AdminState.notify, F.photo)
async def notify_photo(m, state, db, config, bot):
    if not admin_only(config, m.from_user.id):
        return
    caption = m.caption or ''
    if len(caption) > 1024:
        return await m.answer('❌ Подпись к фотографии слишком длинная. Максимум 1024 символа.')
    total, sent, failed = await _broadcast(bot, db, config, caption, m.photo[-1].file_id)
    await state.clear()
    await m.answer(
        f'✅ Оповещение отправлено.\n\n'
        f'Получателей с активной подпиской: {total}\n'
        f'Доставлено: {sent}\n'
        f'Ошибок: {failed}',
        reply_markup=admin_kb()
    )


@router.message(AdminState.notify, F.text)
async def notify_text(m, state, db, config, bot):
    if not admin_only(config, m.from_user.id):
        return
    if len(m.text) > 4096:
        return await m.answer('❌ Сообщение слишком длинное. Максимум 4096 символов.')
    total, sent, failed = await _broadcast(bot, db, config, m.text)
    await state.clear()
    await m.answer(
        f'✅ Оповещение отправлено.\n\n'
        f'Получателей с активной подпиской: {total}\n'
        f'Доставлено: {sent}\n'
        f'Ошибок: {failed}',
        reply_markup=admin_kb()
    )


@router.callback_query(F.data=='admin:tariffs')
async def adm_tariffs(q,db,config):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    rows=await db.tariffs(False); await q.message.edit_text('📦 <b>Тарифы</b>\n\nНажмите на тариф для редактирования.',reply_markup=admin_tariffs_kb(rows),parse_mode='HTML'); await q.answer()

@router.callback_query(F.data.startswith('admintariff:'))
async def tariff_edit(q,db,config,state):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    tid=int(q.data.split(':')[1]); r=await db.tariff(tid)
    await state.set_state(AdminState.edit_tariff); await state.update_data(tariff_id=tid)
    await q.message.answer(f"✏️ Тариф <b>{r['name']}</b>\n\nОтправьте одной строкой:\n<code>название | дни | цена | описание | включен</code>\n\nПример:\n<code>VPN 30d | 30 | 499 ₽ | Основной тариф | 1</code>",parse_mode='HTML'); await q.answer()

@router.callback_query(F.data=='tariff:add')
async def tariff_add(q,state,config):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    await state.set_state(AdminState.add_tariff); await q.message.answer('➕ Отправьте:\n<code>название | дни | цена | описание</code>\nПример: <code>VPN 14d | 14 | 299 ₽ | Доступ на 14 дней</code>',parse_mode='HTML'); await q.answer()

@router.message(AdminState.add_tariff)
async def add_tariff(m,state,db,config):
    if not admin_only(config,m.from_user.id): return
    parts=[x.strip() for x in m.text.split('|')]
    if len(parts)!=4 or not parts[1].isdigit(): return await m.answer('Неверный формат.')
    rows=await db.tariffs(False); await db.execute('INSERT INTO tariffs(name,days,price,description,sort_order) VALUES(?,?,?,?,?)',(parts[0],int(parts[1]),parts[2],parts[3],len(rows)+1)); await state.clear(); await m.answer('✅ Тариф добавлен.',reply_markup=admin_kb())

@router.message(AdminState.edit_tariff)
async def edit_tariff(m,state,db,config):
    if not admin_only(config,m.from_user.id): return
    d=await state.get_data(); parts=[x.strip() for x in m.text.split('|')]
    if len(parts)!=5 or not parts[1].isdigit() or parts[4] not in ('0','1'): return await m.answer('Неверный формат.')
    await db.execute('UPDATE tariffs SET name=?,days=?,price=?,description=?,enabled=? WHERE id=?',(parts[0],int(parts[1]),parts[2],parts[3],int(parts[4]),d['tariff_id'])); await state.clear(); await m.answer('✅ Тариф изменён.',reply_markup=admin_kb())

@router.callback_query(F.data=='admin:settings')
async def settings(q,db,config):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    p=(await db.setting('payment_details',config.payment_details) or '').replace('\\n','\n').replace('\\r','\r'); s=await db.setting('subscription_url_template',config.subscription_url_template)
    await q.message.edit_text(f"⚙️ <b>Настройки</b>\n\n<b>Реквизиты:</b>\n{html.escape(p)}\n\n<b>URL:</b> <code>{html.escape(s)}</code>",reply_markup=settings_kb(),parse_mode='HTML'); await q.answer()

@router.callback_query(F.data=='set:payment')
async def set_payment(q,state,config):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    await state.set_state(AdminState.payment); await q.message.answer('💳 Отправьте новые реквизиты одним сообщением. HTML-теги будут отображаться как текст.'); await q.answer()

@router.message(AdminState.payment)
async def save_payment(m,state,db,config):
    if not admin_only(config,m.from_user.id): return
    await db.set_setting('payment_details',m.text.replace('\\n','\n').replace('\\r','\r')); await state.clear(); await m.answer('✅ Реквизиты сохранены.',reply_markup=admin_kb())

@router.callback_query(F.data=='set:suburl')
async def set_suburl(q,state,config):
    if not admin_only(config,q.from_user.id): return await q.answer('Нет доступа',show_alert=True)
    await state.set_state(AdminState.suburl); await q.message.answer('🔗 Отправьте шаблон URL подписки. Используйте <code>{sub_id}</code>.\nПример: <code>https://enferium.ru/sub/{sub_id}</code>',parse_mode='HTML'); await q.answer()

@router.message(AdminState.suburl)
async def save_suburl(m,state,db,config):
    if not admin_only(config,m.from_user.id): return
    if '{sub_id}' not in m.text or not m.text.startswith(('http://','https://')): return await m.answer('URL должен начинаться с http(s) и содержать {sub_id}.')
    await db.set_setting('subscription_url_template',m.text.strip()); await state.clear(); await m.answer('✅ URL сохранён.',reply_markup=admin_kb())

@router.callback_query(F.data=='admin:subs')
async def admin_subs(q, db, config):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    rows = await db.all(
        """SELECT u.id AS user_id, u.username, u.first_name, COUNT(s.id) AS sub_count
           FROM users u
           JOIN subscriptions s ON s.user_id=u.id AND s.deleted=0
           GROUP BY u.id
           ORDER BY COALESCE(u.first_name, ''), u.id"""
    )
    if not rows:
        await q.message.edit_text('📱 <b>Подписки</b>\n\nПользователей с подписками нет.', reply_markup=admin_kb(), parse_mode='HTML')
        return await q.answer()

    try:
        await q.message.edit_text(
            f'📱 <b>Пользователи</b>\n\nВсего пользователей: <b>{len(rows)}</b>\n\nВыберите пользователя:',
            reply_markup=admin_users_kb(rows), parse_mode='HTML'
        )
    except Exception as e:
        if 'message is not modified' not in str(e).lower():
            raise
    await q.answer()


@router.callback_query(F.data.startswith('admin:user:'))
async def admin_user_subs(q, db, config):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    user_id = int(q.data.split(':')[2])
    user = await db.get('SELECT * FROM users WHERE id=?', (user_id,))
    if not user:
        return await q.answer('Пользователь не найден', show_alert=True)

    rows = await db.all(
        'SELECT * FROM subscriptions WHERE user_id=? AND deleted=0 ORDER BY id DESC',
        (user_id,)
    )
    if not rows:
        return await q.answer('У пользователя нет подписок', show_alert=True)

    name = user['first_name'] or '—'
    username = '@' + user['username'] if user['username'] else 'нет username'
    text = (
        f'👤 <b>{html.escape(name)}</b>\n'
        f'Username: {html.escape(username)}\n'
        f'Telegram ID: <code>{user_id}</code>\n\n'
        f'📱 <b>Подписки пользователя</b>\nВыберите подписку:'
    )
    await q.message.edit_text(text, reply_markup=admin_user_subs_kb(rows, user_id), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('admin:sub:'))
async def admin_sub_detail(q, db, config):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)

    sid = int(q.data.split(':')[2])
    r = await db.get(
        """SELECT s.*, u.username, u.first_name
           FROM subscriptions s
           JOIN users u ON u.id=s.user_id
           WHERE s.id=? AND s.deleted=0""",
        (sid,)
    )
    if not r:
        return await q.answer('Подписка удалена или не найдена', show_alert=True)

    username = '@' + r['username'] if r['username'] else 'нет username'
    status = '🟢 Активна' if r['enabled'] and r['expiry_ms'] > int(time.time()*1000) else ('🟡 Отключена' if not r['enabled'] else '🟠 Истекла')
    text = (
        f'📱 <b>Подписка №{r["id"]}</b>\n\n'
        f'👤 Имя: <b>{html.escape(r["first_name"] or "—")}</b>\n'
        f'Username: {html.escape(username)}\n'
        f'Telegram ID: <code>{r["user_id"]}</code>\n\n'
        f'📊 Статус: <b>{status}</b>\n'
        f'📧 Email: <code>{html.escape(r["xui_email"])}</code>\n'
        f'🔑 Sub ID: <code>{html.escape(r["sub_id"])}</code>\n'
        f'📡 Inbound: <code>{html.escape(r["xui_inbound_ids"] or "—")}</code>\n'
        f'📅 Доступ до: <b>{fmt(r["expiry_ms"])}</b>\n'
        f'🕒 Создана: <b>{html.escape(str(r["created_at"]))}</b>\n'
        f'🔄 Обновлена: <b>{html.escape(str(r["updated_at"]))}</b>'
    )
    await q.message.edit_text(text, reply_markup=admin_sub_detail_kb(sid, bool(r['enabled'])), parse_mode='HTML')
    await q.answer()


@router.callback_query(F.data.startswith('admin:subdelete:'))
async def admin_sub_delete(q, db, config, xui):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)
    sid = int(q.data.split(':')[2])
    r = await db.subscription(sid)
    if not r or r['deleted']:
        return await q.answer('Подписка не найдена', show_alert=True)
    try:
        exists, _, _ = await xui.get_client_state(r['xui_email'])
        if exists:
            await xui.delete_client(r['xui_email'])
        await db.delete_subscription(sid)
    except Exception as e:
        print(f'[ADMIN SUB DELETE] #{sid}: {e}')
        return await q.answer('Не удалось удалить из 3x-ui', show_alert=True)
    await q.answer('Подписка удалена')
    await q.message.edit_text('🗑 <b>Подписка удалена.</b>', reply_markup=admin_kb(), parse_mode='HTML')


@router.callback_query(F.data.startswith('admin:subtoggle:'))
async def admin_sub_toggle(q, db, config, xui):
    if not admin_only(config, q.from_user.id):
        return await q.answer('Нет доступа', show_alert=True)
    sid = int(q.data.split(':')[2])
    r = await db.subscription(sid)
    if not r or r['deleted']:
        return await q.answer('Подписка не найдена', show_alert=True)
    try:
        exists, current_enabled, _ = await xui.get_client_state(r['xui_email'])
        if not exists:
            await db.disable_subscription(sid)
            return await q.answer('Клиент не найден в 3x-ui', show_alert=True)
        new_enabled = not current_enabled
        await xui.set_client_enabled(r['xui_email'], new_enabled)
        if new_enabled:
            await db.enable_subscription(sid)
        else:
            await db.disable_subscription(sid)
    except Exception as e:
        print(f'[ADMIN SUB TOGGLE] #{sid}: {e}')
        return await q.answer('Ошибка 3x-ui: ' + str(e)[:120], show_alert=True)

    # Re-render the same subscription detail.
    user = await db.get('SELECT * FROM users WHERE id=?', (r['user_id'],))
    username = '@' + user['username'] if user and user['username'] else 'нет username'
    status = '🟢 Активна' if new_enabled and r['expiry_ms'] > int(time.time()*1000) else ('🟡 Отключена' if not new_enabled else '🟠 Истекла')
    text = (
        f'📱 <b>Подписка №{r["id"]}</b>\n\n'
        f'👤 Имя: <b>{html.escape((user["first_name"] if user else "") or "—")}</b>\n'
        f'Username: {html.escape(username)}\n'
        f'Telegram ID: <code>{r["user_id"]}</code>\n\n'
        f'📊 Статус: <b>{status}</b>\n'
        f'📧 Email: <code>{html.escape(r["xui_email"])}</code>\n'
        f'🔑 Sub ID: <code>{html.escape(r["sub_id"])}</code>\n'
        f'📡 Inbound: <code>{html.escape(r["xui_inbound_ids"] or "—")}</code>\n'
        f'📅 Доступ до: <b>{fmt(r["expiry_ms"])}</b>\n'
        f'🕒 Создана: <b>{html.escape(str(r["created_at"]))}</b>\n'
        f'🔄 Обновлена: <b>{html.escape(str(r["updated_at"]))}</b>'
    )
    await q.message.edit_text(text, reply_markup=admin_sub_detail_kb(sid, new_enabled), parse_mode='HTML')
    await q.answer('Подписка включена' if new_enabled else 'Подписка отключена')
