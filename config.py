import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _ints(value: str) -> set[int]:
    return {int(x.strip()) for x in (value or '').split(',') if x.strip().isdigit()}

@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int]
    xui_url: str
    xui_username: str
    xui_password: str
    xui_inbound_id: int
    xui_inbound_ids: list[int]
    xui_api_token: str
    subscription_url_template: str
    payment_details: str
    db_path: str


def load_config() -> Config:
    ids = _ints(os.getenv('XUI_INBOUND_IDS', ''))
    if not ids and os.getenv('XUI_INBOUND_ID', '').isdigit():
        ids = {int(os.getenv('XUI_INBOUND_ID'))}
    first = min(ids) if ids else 1
    return Config(
        bot_token=os.getenv('BOT_TOKEN', ''),
        admin_ids=_ints(os.getenv('ADMIN_IDS', '')),
        xui_url=os.getenv('XUI_URL', '').rstrip('/'),
        xui_username=os.getenv('XUI_USERNAME', ''),
        xui_password=os.getenv('XUI_PASSWORD', ''),
        xui_inbound_id=first,
        xui_inbound_ids=sorted(ids or {first}),
        xui_api_token=os.getenv('XUI_API_TOKEN', ''),
        subscription_url_template=os.getenv('SUBSCRIPTION_URL_TEMPLATE', 'https://enferium.ru/sub/{sub_id}'),
        payment_details=os.getenv('PAYMENT_DETAILS', ''),
        db_path=os.getenv('DB_PATH', './data/bot.db'),
    )
