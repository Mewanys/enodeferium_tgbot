import asyncio
import time

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


async def sync_all_subscriptions(db, xui):
    rows = await db.all_enabled_subscriptions()
    for row in rows:
        try:
            exists, xui_enabled, xui_expiry = await xui.get_client_state(row['xui_email'])
            if not exists or not xui_enabled:
                await db.disable_subscription(row['id'])
                print(f'[SUB CHECK] disabled missing/disabled 3x-ui client: #{row["id"]} {row["xui_email"]}')
                continue
            if xui_expiry > 0 and xui_expiry != row['expiry_ms']:
                await db.update_subscription(row['id'], xui_expiry, 1, row['warned_3d'])
        except Exception as e:
            # Fail closed: do not keep showing a subscription that cannot be verified.
            await db.disable_subscription(row['id'])
            print(f'[SUB CHECK] #{row["id"]}: verification failed -> hidden: {e}')


async def notification_loop(bot, db, xui):
    while True:
        try:
            # Synchronize bot state with 3x-ui before checking expirations.
            await sync_all_subscriptions(db, xui)

            now = int(time.time() * 1000)
            rows = await db.expiring_unwarned(now, 3 * 86400000)
            for r in rows:
                days = max(1, (r['expiry_ms'] - now) // 86400000)
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text='🔄 Купить ещё одну', callback_data='tariffs')
                ]])
                try:
                    await bot.send_message(
                        r['user_id'],
                        f"⚠️ <b>Подписка скоро закончится</b>\n\n"
                        f"До окончания осталось примерно {days} дн.\n"
                        "Вы можете купить новую подписку.",
                        parse_mode='HTML',
                        reply_markup=kb,
                    )
                    await db.mark_warned(r['id'])
                except Exception as e:
                    print(f'[NOTIFY #{r["id"]}] {e}')
        except Exception as e:
            print(f'[NOTIFY LOOP] {e}')

        await asyncio.sleep(3600)
