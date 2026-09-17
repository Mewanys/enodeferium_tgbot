import os
from datetime import datetime, timezone

import aiosqlite

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tariffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    days INTEGER NOT NULL,
    price TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    xui_email TEXT UNIQUE NOT NULL,
    sub_id TEXT UNIQUE NOT NULL,
    xui_inbound_ids TEXT NOT NULL,
    expiry_ms INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    warned_3d INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tariff_id INTEGER NOT NULL,
    subscription_id INTEGER,
    amount TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    telegram_file_id TEXT,
    telegram_file_type TEXT,
    admin_id INTEGER,
    admin_comment TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);
'''


class DB:
    def __init__(self, path: str):
        self.path = path
        self.db = None

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        # Migration for existing databases.
        cols = await self.all('PRAGMA table_info(subscriptions)')
        if not any(c['name'] == 'deleted' for c in cols):
            await self.db.execute('ALTER TABLE subscriptions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            await self.db.commit()
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    async def execute(self, sql, params=()):
        cur = await self.db.execute(sql, params)
        await self.db.commit()
        return cur

    async def get(self, sql, params=()):
        cur = await self.db.execute(sql, params)
        return await cur.fetchone()

    async def all(self, sql, params=()):
        cur = await self.db.execute(sql, params)
        return await cur.fetchall()

    async def setting(self, key, default=''):
        row = await self.get('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default

    async def set_setting(self, key, value):
        await self.execute(
            'INSERT INTO settings(key,value) VALUES(?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, value)
        )

    async def ensure_defaults(self, payment_details, sub_template):
        if not await self.setting('payment_details'):
            await self.set_setting('payment_details', payment_details)

        current_url = await self.setting('subscription_url_template')
        # Migrate the template from the old project version, while preserving
        # any URL the administrator has deliberately set in the admin panel.
        if not current_url or current_url in {
            'https://enferium.ru/sub/{sub_id}',
            'https://enferium.ru/sub/{subId}',
            'https://ru.enferium.ru:2026/{sub_id}',
        }:
            await self.set_setting('subscription_url_template', sub_template)

        row = await self.get('SELECT COUNT(*) c FROM tariffs')
        if row['c'] == 0:
            await self.execute(
                "INSERT INTO tariffs(name,days,price,description,sort_order) "
                "VALUES('VPN 30d',30,'100 ₽','Доступ на 30 дней',1)"
            )

    async def upsert_user(self, tg_user):
        await self.execute(
            '''INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name''',
            (tg_user.id, tg_user.username, tg_user.first_name or '', datetime.now(timezone.utc).isoformat())
        )

    async def tariffs(self, enabled_only=True):
        q = 'SELECT * FROM tariffs' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY sort_order,id'
        return await self.all(q)

    async def tariff(self, tariff_id):
        return await self.get('SELECT * FROM tariffs WHERE id=?', (tariff_id,))

    async def all_subscriptions_for_user(self, user_id):
        return await self.all(
            'SELECT * FROM subscriptions WHERE user_id=? ORDER BY expiry_ms DESC',
            (user_id,)
        )

    async def subscriptions_for_user(self, user_id):
        return await self.all(
            'SELECT * FROM subscriptions WHERE user_id=? AND enabled=1 ORDER BY expiry_ms DESC',
            (user_id,)
        )

    async def all_enabled_subscriptions(self):
        return await self.all('SELECT * FROM subscriptions WHERE enabled=1 ORDER BY expiry_ms')

    async def active_subscriber_ids(self):
        rows = await self.all('SELECT DISTINCT user_id FROM subscriptions WHERE enabled=1')
        return [r['user_id'] for r in rows]

    async def subscription(self, sid):
        return await self.get('SELECT * FROM subscriptions WHERE id=?', (sid,))

    async def subscription_by_user(self, user_id, sub_id):
        return await self.get(
            'SELECT * FROM subscriptions WHERE user_id=? AND id=?',
            (user_id, sub_id)
        )

    async def add_subscription(self, user_id, email, sub_id, inbound_ids, expiry_ms):
        now = datetime.now(timezone.utc).isoformat()
        cur = await self.execute(
            '''INSERT INTO subscriptions(
                   user_id,xui_email,sub_id,xui_inbound_ids,expiry_ms,enabled,
                   warned_3d,created_at,updated_at
               ) VALUES(?,?,?,?,?,1,0,?,?)''',
            (user_id, email, sub_id, ','.join(map(str, inbound_ids)), expiry_ms, now, now)
        )
        return cur.lastrowid

    async def update_subscription(self, sid, expiry_ms, enabled=1, warned_3d=0):
        await self.execute(
            'UPDATE subscriptions SET expiry_ms=?,enabled=?,warned_3d=?,updated_at=? WHERE id=?',
            (expiry_ms, enabled, warned_3d, datetime.now(timezone.utc).isoformat(), sid)
        )

    async def disable_subscription(self, sid):
        await self.execute(
            'UPDATE subscriptions SET enabled=0,updated_at=? WHERE id=?',
            (datetime.now(timezone.utc).isoformat(), sid)
        )

    async def enable_subscription(self, sid):
        await self.execute(
            'UPDATE subscriptions SET enabled=1,updated_at=? WHERE id=?',
            (datetime.now(timezone.utc).isoformat(), sid)
        )

    async def delete_subscription(self, sid):
        await self.execute(
            'UPDATE subscriptions SET enabled=0,deleted=1,updated_at=? WHERE id=?',
            (datetime.now(timezone.utc).isoformat(), sid)
        )

    async def pending_payments(self):
        return await self.all(
            '''SELECT p.*,u.username,u.first_name,t.name tariff_name,t.days,t.price FROM payments p
               JOIN users u ON u.id=p.user_id JOIN tariffs t ON t.id=p.tariff_id
               WHERE p.status='pending' ORDER BY p.id'''
        )

    async def payment(self, pid):
        return await self.get(
            '''SELECT p.*,u.username,u.first_name,t.name tariff_name,t.days,t.price FROM payments p
               JOIN users u ON u.id=p.user_id JOIN tariffs t ON t.id=p.tariff_id WHERE p.id=?''',
            (pid,)
        )

    async def create_payment(self, user_id, tariff_id, subscription_id, amount):
        cur = await self.execute(
            '''INSERT INTO payments(user_id,tariff_id,subscription_id,amount,created_at)
               VALUES(?,?,?,?,?)''',
            (user_id, tariff_id, subscription_id, amount, datetime.now(timezone.utc).isoformat())
        )
        return cur.lastrowid

    async def attach_receipt(self, pid, file_id, file_type):
        await self.execute(
            'UPDATE payments SET telegram_file_id=?,telegram_file_type=? WHERE id=?',
            (file_id, file_type, pid)
        )

    async def process_payment(self, pid, status, admin_id, comment=''):
        await self.execute(
            'UPDATE payments SET status=?,admin_id=?,admin_comment=?,processed_at=? WHERE id=?',
            (status, admin_id, comment, datetime.now(timezone.utc).isoformat(), pid)
        )

    async def expiring_unwarned(self, now_ms, three_days_ms):
        return await self.all(
            '''SELECT * FROM subscriptions
               WHERE enabled=1 AND warned_3d=0 AND expiry_ms>? AND expiry_ms<=?''',
            (now_ms, now_ms + three_days_ms)
        )

    async def mark_warned(self, sid):
        await self.execute('UPDATE subscriptions SET warned_3d=1 WHERE id=?', (sid,))
