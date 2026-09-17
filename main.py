import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import load_config
from database import DB
from xui import XUI
from handlers.user import router as user_router
from handlers.admin import router as admin_router
from services.notifications import notification_loop

async def main():
    logging.basicConfig(level=logging.INFO)
    config=load_config()
    if not config.bot_token: raise RuntimeError('BOT_TOKEN is empty')
    db=DB(config.db_path); await db.connect(); await db.ensure_defaults(config.payment_details,config.subscription_url_template)
    xui = XUI(
        config.xui_url,
        config.xui_username,
        config.xui_password,
        config.xui_api_token,
    )
    await xui.login()
    bot=Bot(config.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    dp.include_router(user_router); dp.include_router(admin_router)
    # Inject dependencies into every handler
    dp['db']=db; dp['config']=config; dp['xui']=xui; dp['bot']=bot
    task=asyncio.create_task(notification_loop(bot,db,xui))
    try: await dp.start_polling(bot)
    finally:
        task.cancel(); await xui.close(); await db.close(); await bot.session.close()

if __name__=='__main__': asyncio.run(main())
