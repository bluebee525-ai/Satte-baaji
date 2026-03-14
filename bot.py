"""
Real LTC Discord Gambling Bot — Railway Edition
All config via environment variables. See .env.example for the full list.
pip install discord.py aiohttp bit
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3, random, os, json
from datetime import datetime
import aiohttp

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG — every value comes from an environment variable
# ══════════════════════════════════════════════════════════════════════════════

# ── Required secrets ──────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.environ["DISCORD_TOKEN"]           # Discord bot token
BLOCKCYPHER_TOKEN  = os.environ["BLOCKCYPHER_TOKEN"]        # BlockCypher API token

# ── Network ───────────────────────────────────────────────────────────────────
# "ltc" = mainnet real money | "ltc3" = testnet (free fake LTC)
LTC_NETWORK        = os.getenv("LTC_NETWORK",        "ltc")

# ── Database ──────────────────────────────────────────────────────────────────
# On Railway use /app/data/gambling.db so it survives deploys with a volume
DB_PATH            = os.getenv("DB_PATH",             "/app/data/gambling.db")

# ── Deposit / withdrawal limits ───────────────────────────────────────────────
MIN_DEPOSIT        = float(os.getenv("MIN_DEPOSIT",   "0.001"))   # LTC
MIN_WITHDRAW       = float(os.getenv("MIN_WITHDRAW",  "0.001"))   # LTC
WITHDRAW_FEE       = float(os.getenv("WITHDRAW_FEE",  "0.0001"))  # LTC flat fee
REQUIRED_CONFS     = int(  os.getenv("REQUIRED_CONFS","1"))       # confirmations before credit
DEPOSIT_POLL_SEC   = int(  os.getenv("DEPOSIT_POLL_SEC","60"))    # seconds between deposit polls

# ── House edge & bet limits ───────────────────────────────────────────────────
HOUSE_EDGE         = float(os.getenv("HOUSE_EDGE",    "0.01"))    # 1%
MIN_BET            = float(os.getenv("MIN_BET",       "0.00001")) # LTC
MAX_BET            = float(os.getenv("MAX_BET",       "10.0"))    # LTC (0 = unlimited)

# ── Slots paytable multipliers ────────────────────────────────────────────────
SLOTS_CHERRY       = float(os.getenv("SLOTS_CHERRY",  "2"))
SLOTS_LEMON        = float(os.getenv("SLOTS_LEMON",   "3"))
SLOTS_BELL         = float(os.getenv("SLOTS_BELL",    "5"))
SLOTS_DIAMOND      = float(os.getenv("SLOTS_DIAMOND", "10"))
SLOTS_SEVEN        = float(os.getenv("SLOTS_SEVEN",   "20"))
SLOTS_CLOVER       = float(os.getenv("SLOTS_CLOVER",  "50"))
SLOTS_PAIR_MULT    = float(os.getenv("SLOTS_PAIR_MULT","1.5"))    # any two matching

# ── Dice limits ───────────────────────────────────────────────────────────────
DICE_MIN_TARGET    = int(  os.getenv("DICE_MIN_TARGET","2"))
DICE_MAX_TARGET    = int(  os.getenv("DICE_MAX_TARGET","98"))

# ── Crash limits ─────────────────────────────────────────────────────────────
CRASH_MIN_CASHOUT  = float(os.getenv("CRASH_MIN_CASHOUT","1.01"))

# ── Limbo limits ─────────────────────────────────────────────────────────────
LIMBO_MIN_TARGET   = float(os.getenv("LIMBO_MIN_TARGET","1.01"))
LIMBO_MAX_TARGET   = float(os.getenv("LIMBO_MAX_TARGET","1000000"))

# ── Mines limits ─────────────────────────────────────────────────────────────
MINES_GRID         = int(  os.getenv("MINES_GRID",    "25"))      # total tiles (5x5)
MINES_MIN          = int(  os.getenv("MINES_MIN",     "1"))
MINES_MAX          = int(  os.getenv("MINES_MAX",     "24"))

# ── Tic Tac Toe ───────────────────────────────────────────────────────────────
TTT_ACCEPT_TIMEOUT = int(  os.getenv("TTT_ACCEPT_TIMEOUT","120")) # seconds to accept challenge
TTT_MOVE_TIMEOUT   = int(  os.getenv("TTT_MOVE_TIMEOUT",  "300")) # seconds per move

# ── Admin ─────────────────────────────────────────────────────────────────────
# Comma-separated Discord user IDs e.g. "123456789,987654321"
_admin_raw          = os.getenv("ADMIN_IDS", "")
ADMIN_IDS           = [int(x.strip()) for x in _admin_raw.split(",") if x.strip()]

# ── Bot presence ─────────────────────────────────────────────────────────────
BOT_STATUS         = os.getenv("BOT_STATUS", "🎰 LTC Gambling | /help")

# ══════════════════════════════════════════════════════════════════════════════
#  DERIVED / COMPUTED VALUES
# ══════════════════════════════════════════════════════════════════════════════
BC_BASE = f"https://api.blockcypher.com/v1/{LTC_NETWORK}/main"

SLOTS_PAYOUTS = {
    "🍒🍒🍒": SLOTS_CHERRY,
    "🍋🍋🍋": SLOTS_LEMON,
    "🔔🔔🔔": SLOTS_BELL,
    "💎💎💎": SLOTS_DIAMOND,
    "7️⃣7️⃣7️⃣": SLOTS_SEVEN,
    "🍀🍀🍀": SLOTS_CLOVER,
}

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════
def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            balance       REAL    DEFAULT 0.0,
            ltc_address   TEXT    UNIQUE,
            ltc_privkey   TEXT,
            total_wagered REAL    DEFAULT 0.0,
            total_won     REAL    DEFAULT 0.0,
            games_played  INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            txid          TEXT    UNIQUE,
            amount_ltc    REAL,
            confirmations INTEGER DEFAULT 0,
            credited      INTEGER DEFAULT 0,
            timestamp     TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS withdrawals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            txid          TEXT,
            amount_ltc    REAL,
            to_address    TEXT,
            status        TEXT    DEFAULT 'pending',
            timestamp     TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS bets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            game          TEXT,
            bet           REAL,
            profit        REAL,
            result        TEXT,
            timestamp     TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ttt_games (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger    INTEGER,
            opponent      INTEGER,
            bet           REAL,
            board         TEXT    DEFAULT '         ',
            turn          INTEGER,
            status        TEXT    DEFAULT 'waiting',
            winner        INTEGER DEFAULT NULL,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mines_games (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
            bet           REAL,
            mines         INTEGER,
            board         TEXT,
            revealed      TEXT    DEFAULT '0000000000000000000000000',
            status        TEXT    DEFAULT 'active',
            current_mult  REAL    DEFAULT 1.0,
            gems_found    INTEGER DEFAULT 0,
            created_at    TEXT    DEFAULT CURRENT_TIMESTAMP
        );
        """)
        c.commit()

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_user(uid: int, username: str) -> dict:
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            c.execute("INSERT INTO users (user_id, username) VALUES (?,?)", (uid, str(username)))
            c.commit()
            row = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        return dict(row)

def add_balance(uid: int, delta: float):
    with db() as c:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, uid))
        c.commit()

def record_bet(uid: int, game: str, bet: float, profit: float, result: str):
    with db() as c:
        won = max(0.0, profit + bet)
        c.execute("""UPDATE users SET
            balance=balance+?, total_wagered=total_wagered+?,
            total_won=total_won+?, games_played=games_played+1
            WHERE user_id=?""", (profit, bet, won, uid))
        c.execute("INSERT INTO bets (user_id,game,bet,profit,result) VALUES (?,?,?,?,?)",
                  (uid, game, bet, profit, result))
        c.commit()

def validate_bet(user: dict, bet: float) -> str | None:
    if bet <= 0:
        return "Bet must be > 0."
    if bet < MIN_BET:
        return f"Minimum bet is {ltc(MIN_BET)}."
    if MAX_BET > 0 and bet > MAX_BET:
        return f"Maximum bet is {ltc(MAX_BET)}."
    if bet > user["balance"]:
        return f"Insufficient balance. You have {ltc(user['balance'])}."
    return None

def ltc(v: float) -> str:
    return f"Ł{v:.6f}"

# ══════════════════════════════════════════════════════════════════════════════
#  BLOCKCYPHER API
# ══════════════════════════════════════════════════════════════════════════════
async def bc_post(session: aiohttp.ClientSession, path: str, data: dict) -> dict:
    url = f"{BC_BASE}{path}?token={BLOCKCYPHER_TOKEN}"
    async with session.post(url, json=data) as r:
        return await r.json()

async def bc_get(session: aiohttp.ClientSession, path: str) -> dict:
    url = f"{BC_BASE}{path}?token={BLOCKCYPHER_TOKEN}"
    async with session.get(url) as r:
        return await r.json()

async def create_ltc_address(session: aiohttp.ClientSession):
    data = await bc_post(session, "/addrs", {})
    return data.get("address"), data.get("private"), data.get("wif")

async def get_address_txs(session: aiohttp.ClientSession, address: str) -> list:
    data = await bc_get(session, f"/addrs/{address}/full")
    return data.get("txs", [])

async def broadcast_withdrawal(session: aiohttp.ClientSession,
                                wif: str, to_address: str, amount_ltc: float) -> str | None:
    """Build, sign, and broadcast a withdrawal TX via BlockCypher."""
    import ecdsa
    satoshis = int(amount_ltc * 1e8)
    # Step 1 — build unsigned TX skeleton
    newtx = await bc_post(session, "/txs/new", {
        "inputs":  [{"addresses": []}],   # BlockCypher fills from WIF-derived address
        "outputs": [{"addresses": [to_address], "value": satoshis}],
    })
    if "errors" in newtx:
        print(f"[withdraw] TX build error: {newtx['errors']}")
        return None
    # Step 2 — sign each tosign hash
    sk         = ecdsa.SigningKey.from_string(bytes.fromhex(wif), curve=ecdsa.SECP256k1)
    vk         = sk.get_verifying_key()
    pub_hex    = ("02" if vk.pubkey.point.y() % 2 == 0 else "03") + format(vk.pubkey.point.x(), "064x")
    signatures = []
    pubkeys    = []
    for ts in newtx.get("tosign", []):
        sig = sk.sign_digest(bytes.fromhex(ts), sigencode=ecdsa.util.sigencode_der)
        signatures.append(sig.hex() + "01")   # SIGHASH_ALL
        pubkeys.append(pub_hex)
    newtx["signatures"] = signatures
    newtx["pubkeys"]    = pubkeys
    # Step 3 — send signed TX
    sent = await bc_post(session, "/txs/send", newtx)
    return sent.get("tx", {}).get("hash")

# ══════════════════════════════════════════════════════════════════════════════
#  BOT
# ══════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    poll_deposits.start()
    process_withdrawals.start()
    print(f"✅  {bot.user}  |  Network: {LTC_NETWORK.upper()}  |  DB: {DB_PATH}")
    await bot.change_presence(activity=discord.Game(BOT_STATUS))

# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════
@tasks.loop(seconds=DEPOSIT_POLL_SEC)
async def poll_deposits():
    with db() as c:
        users = c.execute(
            "SELECT user_id, ltc_address FROM users WHERE ltc_address IS NOT NULL"
        ).fetchall()
    if not users:
        return
    async with aiohttp.ClientSession() as session:
        for u in users:
            try:
                txs = await get_address_txs(session, u["ltc_address"])
                for tx in txs:
                    txid  = tx.get("hash")
                    confs = tx.get("confirmations", 0)
                    if not txid or confs < REQUIRED_CONFS:
                        continue
                    amount_sat = sum(
                        out.get("value", 0)
                        for out in tx.get("outputs", [])
                        if u["ltc_address"] in out.get("addresses", [])
                    )
                    amount_ltc = amount_sat / 1e8
                    if amount_ltc < MIN_DEPOSIT:
                        continue
                    with db() as c:
                        existing = c.execute(
                            "SELECT credited FROM deposits WHERE txid=?", (txid,)
                        ).fetchone()
                        if existing and existing["credited"]:
                            continue
                        if not existing:
                            c.execute(
                                "INSERT OR IGNORE INTO deposits "
                                "(user_id,txid,amount_ltc,confirmations,credited) VALUES (?,?,?,?,0)",
                                (u["user_id"], txid, amount_ltc, confs)
                            )
                        c.execute(
                            "UPDATE deposits SET credited=1, confirmations=? WHERE txid=? AND credited=0",
                            (confs, txid)
                        )
                        affected = c.execute(
                            "SELECT changes() as n"
                        ).fetchone()["n"]
                        if affected:
                            c.execute(
                                "UPDATE users SET balance=balance+? WHERE user_id=?",
                                (amount_ltc, u["user_id"])
                            )
                        c.commit()
                    if affected:
                        try:
                            user_obj = await bot.fetch_user(u["user_id"])
                            await user_obj.send(
                                f"✅ **Deposit confirmed!** +{ltc(amount_ltc)} credited.\n"
                                f"TX: `{txid}`"
                            )
                        except Exception:
                            pass
            except Exception as e:
                print(f"[poll_deposits] {u['ltc_address']}: {e}")

@tasks.loop(seconds=30)
async def process_withdrawals():
    """Pick up pending withdrawals and broadcast them on-chain."""
    with db() as c:
        pending = c.execute(
            "SELECT w.*, u.ltc_privkey FROM withdrawals w "
            "JOIN users u ON u.user_id=w.user_id "
            "WHERE w.status='pending' AND w.txid IS NULL"
        ).fetchall()
    if not pending:
        return
    async with aiohttp.ClientSession() as session:
        for row in pending:
            try:
                txid = await broadcast_withdrawal(
                    session, row["ltc_privkey"], row["to_address"], row["amount_ltc"]
                )
                status = "broadcast" if txid else "failed"
                with db() as c:
                    c.execute(
                        "UPDATE withdrawals SET txid=?, status=? WHERE id=?",
                        (txid, status, row["id"])
                    )
                    c.commit()
                try:
                    user_obj = await bot.fetch_user(row["user_id"])
                    if txid:
                        await user_obj.send(
                            f"📤 **Withdrawal sent!** {ltc(row['amount_ltc'])} → `{row['to_address']}`\n"
                            f"TX: `{txid}`"
                        )
                    else:
                        # Refund on failure
                        add_balance(row["user_id"], row["amount_ltc"] + WITHDRAW_FEE)
                        await user_obj.send(
                            f"⚠️ Withdrawal of {ltc(row['amount_ltc'])} failed and has been refunded."
                        )
                except Exception:
                    pass
            except Exception as e:
                print(f"[process_withdrawals] row {row['id']}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

# ── /deposit ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="deposit", description="Get your personal LTC deposit address")
async def deposit(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = get_user(interaction.user.id, str(interaction.user))
    if user["ltc_address"]:
        embed = discord.Embed(title="💳 Your LTC Deposit Address", color=0x345D9D)
        embed.add_field(name="Address",                value=f"`{user['ltc_address']}`", inline=False)
        embed.add_field(name="Min Deposit",            value=ltc(MIN_DEPOSIT),           inline=True)
        embed.add_field(name="Confirmations Required", value=str(REQUIRED_CONFS),        inline=True)
        embed.set_footer(text=f"Send LTC to this address. Credits after {REQUIRED_CONFS} confirmation(s).")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    async with aiohttp.ClientSession() as session:
        address, _, wif = await create_ltc_address(session)
    if not address:
        await interaction.followup.send("❌ Failed to generate address. Try again later.", ephemeral=True)
        return
    with db() as c:
        c.execute("UPDATE users SET ltc_address=?, ltc_privkey=? WHERE user_id=?",
                  (address, wif, interaction.user.id))
        c.commit()
    embed = discord.Embed(title="💳 Your LTC Deposit Address", color=0x345D9D)
    embed.add_field(name="Address",                value=f"`{address}`",    inline=False)
    embed.add_field(name="Min Deposit",            value=ltc(MIN_DEPOSIT),  inline=True)
    embed.add_field(name="Confirmations Required", value=str(REQUIRED_CONFS), inline=True)
    embed.set_footer(text=f"Send LTC to this address. Credits after {REQUIRED_CONFS} confirmation(s).")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /withdraw ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="withdraw", description="Withdraw LTC to an external address")
@app_commands.describe(amount="LTC amount to withdraw", address="Your external LTC address")
async def withdraw(interaction: discord.Interaction, amount: float, address: str):
    await interaction.response.defer(ephemeral=True)
    user  = get_user(interaction.user.id, str(interaction.user))
    total = amount + WITHDRAW_FEE
    if amount < MIN_WITHDRAW:
        await interaction.followup.send(f"❌ Minimum withdrawal is {ltc(MIN_WITHDRAW)}.", ephemeral=True); return
    if total > user["balance"]:
        await interaction.followup.send(
            f"❌ Insufficient balance. Need {ltc(total)} (includes {ltc(WITHDRAW_FEE)} fee).", ephemeral=True); return
    if not user["ltc_address"] or not user["ltc_privkey"]:
        await interaction.followup.send("❌ No deposit address on file. Use /deposit first.", ephemeral=True); return
    add_balance(interaction.user.id, -total)
    with db() as c:
        c.execute(
            "INSERT INTO withdrawals (user_id, amount_ltc, to_address, status) VALUES (?,?,?,'pending')",
            (interaction.user.id, amount, address)
        )
        c.commit()
    embed = discord.Embed(title="📤 Withdrawal Queued", color=0xFEE75C)
    embed.add_field(name="Amount",  value=ltc(amount),       inline=True)
    embed.add_field(name="Fee",     value=ltc(WITHDRAW_FEE), inline=True)
    embed.add_field(name="To",      value=f"`{address}`",    inline=False)
    embed.add_field(name="Status",  value="Pending — will broadcast within 30 seconds", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /balance ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="balance", description="Check your LTC balance")
async def balance(interaction: discord.Interaction):
    user = get_user(interaction.user.id, str(interaction.user))
    pnl  = user["total_won"] - user["total_wagered"]
    embed = discord.Embed(title="💰 Balance", color=0xA8C8A0)
    embed.add_field(name="Balance",       value=ltc(user["balance"]),       inline=True)
    embed.add_field(name="Total Wagered", value=ltc(user["total_wagered"]), inline=True)
    embed.add_field(name="Total Won",     value=ltc(user["total_won"]),     inline=True)
    embed.add_field(name="Games Played",  value=str(user["games_played"]),  inline=True)
    embed.add_field(name="Net P/L",       value=ltc(pnl),                   inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /dice ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="dice", description="Roll dice — bet over or under a target")
@app_commands.describe(
    bet=f"LTC amount (min/max set by server)",
    target=f"Target number",
    direction="over or under"
)
@app_commands.choices(direction=[
    app_commands.Choice(name="over",  value="over"),
    app_commands.Choice(name="under", value="under"),
])
async def dice(interaction: discord.Interaction, bet: float, target: int, direction: str):
    user = get_user(interaction.user.id, str(interaction.user))
    err  = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return
    if not DICE_MIN_TARGET <= target <= DICE_MAX_TARGET:
        await interaction.response.send_message(
            f"❌ Target must be {DICE_MIN_TARGET}–{DICE_MAX_TARGET}.", ephemeral=True); return

    roll       = round(random.uniform(0.01, 99.99), 2)
    win_chance = ((99 - target) if direction == "over" else (target - 1)) / 100
    mult       = round((1 - HOUSE_EDGE) / win_chance, 4)
    win        = (roll > target) if direction == "over" else (roll < target)
    profit     = round(bet * mult - bet, 8) if win else -bet

    record_bet(interaction.user.id, "dice", bet, profit, str(roll))
    embed = discord.Embed(title="🎲 Dice", color=0x57F287 if win else 0xED4245)
    embed.add_field(name="Roll",    value=f"**{roll}**",                             inline=True)
    embed.add_field(name="Target",  value=f"{direction} {target}",                  inline=True)
    embed.add_field(name="Mult",    value=f"{mult}×",                               inline=True)
    embed.add_field(name="Bet",     value=ltc(bet),                                 inline=True)
    embed.add_field(name="Profit",  value=f"{'+'if win else ''}{ltc(profit)}",      inline=True)
    embed.add_field(name="Balance", value=ltc(user["balance"] + profit),            inline=True)
    embed.set_footer(text=f"Win chance: {win_chance*100:.2f}%  |  House edge: {HOUSE_EDGE*100}%")
    await interaction.response.send_message(embed=embed)

# ── /coinflip ─────────────────────────────────────────────────────────────────
@bot.tree.command(name="coinflip", description="Heads or tails")
@app_commands.describe(bet="LTC amount", side="heads or tails")
@app_commands.choices(side=[
    app_commands.Choice(name="heads", value="heads"),
    app_commands.Choice(name="tails", value="tails"),
])
async def coinflip(interaction: discord.Interaction, bet: float, side: str):
    user   = get_user(interaction.user.id, str(interaction.user))
    err    = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return

    result = random.choice(["heads", "tails"])
    win    = result == side
    payout = round(1 - HOUSE_EDGE, 8)
    profit = round(bet * payout, 8) if win else -bet

    record_bet(interaction.user.id, "coinflip", bet, profit, result)
    embed = discord.Embed(title="🪙 Coinflip", color=0x57F287 if win else 0xED4245)
    embed.add_field(name="Result",  value=result.capitalize(),                  inline=True)
    embed.add_field(name="You bet", value=side.capitalize(),                    inline=True)
    embed.add_field(name="Payout",  value=f"{1+payout:.4f}×",                  inline=True)
    embed.add_field(name="Profit",  value=f"{'+'if win else ''}{ltc(profit)}", inline=True)
    embed.add_field(name="Balance", value=ltc(user["balance"] + profit),       inline=True)
    await interaction.response.send_message(embed=embed)

# ── /crash ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="crash", description="Crash — set your auto-cashout multiplier")
@app_commands.describe(bet="LTC amount", cashout=f"Auto-cashout multiplier (min {CRASH_MIN_CASHOUT})")
async def crash(interaction: discord.Interaction, bet: float, cashout: float):
    user = get_user(interaction.user.id, str(interaction.user))
    err  = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return
    if cashout < CRASH_MIN_CASHOUT:
        await interaction.response.send_message(
            f"❌ Cashout must be ≥ {CRASH_MIN_CASHOUT}×", ephemeral=True); return

    r           = random.uniform(0, 1 - HOUSE_EDGE)
    crash_point = max(1.0, round(1 / (1 - r), 2))
    win         = crash_point >= cashout
    profit      = round(bet * cashout - bet, 8) if win else -bet

    record_bet(interaction.user.id, "crash", bet, profit, str(crash_point))
    embed = discord.Embed(
        title="🚀 Crash",
        description=f"Crashed at **{crash_point}×**",
        color=0x57F287 if win else 0xED4245
    )
    embed.add_field(name="Your Cashout", value=f"{cashout}×",                      inline=True)
    embed.add_field(name="Crash Point",  value=f"{crash_point}×",                  inline=True)
    embed.add_field(name="Profit",  value=f"{'+'if win else ''}{ltc(profit)}",     inline=True)
    embed.add_field(name="Balance", value=ltc(user["balance"] + profit),           inline=True)
    await interaction.response.send_message(embed=embed)

# ── /limbo ────────────────────────────────────────────────────────────────────
@bot.tree.command(name="limbo", description="Limbo — roll must reach your target multiplier")
@app_commands.describe(bet="LTC amount", target="Target multiplier e.g. 3.0")
async def limbo(interaction: discord.Interaction, bet: float, target: float):
    user = get_user(interaction.user.id, str(interaction.user))
    err  = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return
    if not LIMBO_MIN_TARGET <= target <= LIMBO_MAX_TARGET:
        await interaction.response.send_message(
            f"❌ Target must be {LIMBO_MIN_TARGET}–{LIMBO_MAX_TARGET}×", ephemeral=True); return

    r           = random.uniform(0, 1 - HOUSE_EDGE)
    result_mult = round(1 / (1 - r), 4)
    win         = result_mult >= target
    profit      = round(bet * target - bet, 8) if win else -bet
    win_pct     = round((1 - HOUSE_EDGE) / target * 100, 4)

    record_bet(interaction.user.id, "limbo", bet, profit, str(result_mult))
    embed = discord.Embed(title="🌀 Limbo", color=0x57F287 if win else 0xED4245)
    embed.add_field(name="Result",  value=f"**{result_mult}×**",               inline=True)
    embed.add_field(name="Target",  value=f"{target}×",                        inline=True)
    embed.add_field(name="Profit",  value=f"{'+'if win else ''}{ltc(profit)}", inline=True)
    embed.add_field(name="Balance", value=ltc(user["balance"] + profit),       inline=True)
    embed.set_footer(text=f"Win chance: {win_pct}%  |  House edge: {HOUSE_EDGE*100}%")
    await interaction.response.send_message(embed=embed)

# ── /slots ────────────────────────────────────────────────────────────────────
SLOT_SYMBOLS = ["🍒","🍋","🔔","💎","7️⃣","🍀"]
SLOT_WEIGHTS = [30,  25,  20,  12,   8,    5]

@bot.tree.command(name="slots", description="Spin the slot machine")
@app_commands.describe(bet="LTC amount")
async def slots(interaction: discord.Interaction, bet: float):
    user  = get_user(interaction.user.id, str(interaction.user))
    err   = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return

    reels  = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    combo  = "".join(reels)
    mult   = SLOTS_PAYOUTS.get(combo, 0.0)
    if mult == 0.0 and len(set(reels)) < 3:
        mult = SLOTS_PAIR_MULT
    profit = round(bet * mult - bet, 8) if mult > 0 else -bet

    record_bet(interaction.user.id, "slots", bet, profit, combo)
    color = 0xFFD700 if mult >= SLOTS_DIAMOND else (0x57F287 if profit > 0 else 0xED4245)
    embed = discord.Embed(title="🎰 Slots", description=f"## {' '.join(reels)}", color=color)
    embed.add_field(name="Multiplier", value=f"{mult}×" if mult > 0 else "0×",    inline=True)
    embed.add_field(name="Profit",  value=f"{'+'if profit>0 else ''}{ltc(profit)}", inline=True)
    embed.add_field(name="Balance", value=ltc(user["balance"] + profit),           inline=True)
    if combo in SLOTS_PAYOUTS and mult >= SLOTS_SEVEN:
        embed.set_footer(text="🎉 JACKPOT!")
    await interaction.response.send_message(embed=embed)

# ── /mines ────────────────────────────────────────────────────────────────────
def mines_multiplier(gems_found: int, mines: int) -> float:
    total = MINES_GRID
    safe  = total - mines
    try:
        p = 1.0
        for i in range(gems_found):
            p *= (safe - i) / (total - i)
        return round((1 - HOUSE_EDGE) / p, 4) if p > 0 else 9999.0
    except ZeroDivisionError:
        return 9999.0

class MinesView(discord.ui.View):
    def __init__(self, game_id: int, user_id: int, board: list):
        super().__init__(timeout=TTT_MOVE_TIMEOUT)
        self.game_id = game_id
        self.user_id = user_id
        cols = int(MINES_GRID ** 0.5)
        for i in range(MINES_GRID):
            btn = discord.ui.Button(
                label="⬛", row=i // cols,
                custom_id=f"mine_{game_id}_{i}",
                style=discord.ButtonStyle.secondary
            )
            btn.callback = self._make_tile_cb(i)
            self.add_item(btn)
        cashout_btn = discord.ui.Button(
            label="💰 Cash Out", style=discord.ButtonStyle.success,
            row=cols, custom_id=f"cashout_{game_id}"
        )
        cashout_btn.callback = self._cashout_cb
        self.add_item(cashout_btn)

    def _make_tile_cb(self, tile: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("Not your game.", ephemeral=True); return
            with db() as c:
                g = c.execute("SELECT * FROM mines_games WHERE id=?", (self.game_id,)).fetchone()
            if not g or g["status"] != "active":
                await interaction.response.send_message("Game already ended.", ephemeral=True); return
            revealed = list(g["revealed"])
            board    = json.loads(g["board"])
            if revealed[tile] == "1":
                await interaction.response.send_message("Already revealed.", ephemeral=True); return
            revealed[tile] = "1"
            rev_str = "".join(revealed)
            if board[tile] == "M":
                record_bet(self.user_id, "mines", g["bet"], -g["bet"], "loss")
                with db() as c:
                    c.execute("UPDATE mines_games SET status='lost', revealed=? WHERE id=?",
                              (rev_str, self.game_id))
                    c.commit()
                embed = discord.Embed(title="💣 BOOM! You hit a mine!", color=0xED4245)
                embed.add_field(name="Bet Lost", value=ltc(g["bet"]))
                for child in self.children: child.disabled = True
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                gems = g["gems_found"] + 1
                mult = mines_multiplier(gems, g["mines"])
                with db() as c:
                    c.execute(
                        "UPDATE mines_games SET revealed=?, gems_found=?, current_mult=? WHERE id=?",
                        (rev_str, gems, mult, self.game_id)
                    )
                    c.commit()
                for child in self.children:
                    if getattr(child, "custom_id", None) == f"mine_{self.game_id}_{tile}":
                        child.label = "💎"; child.style = discord.ButtonStyle.success; child.disabled = True
                embed = discord.Embed(title="💎 Mines", color=0x57F287)
                embed.add_field(name="Gems",       value=str(gems),                  inline=True)
                embed.add_field(name="Multiplier", value=f"{mult}×",                 inline=True)
                embed.add_field(name="If cashed",  value=ltc(round(g["bet"]*mult,8)),inline=True)
                await interaction.response.edit_message(embed=embed, view=self)
        return cb

    async def _cashout_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your game.", ephemeral=True); return
        with db() as c:
            g = c.execute("SELECT * FROM mines_games WHERE id=?", (self.game_id,)).fetchone()
        if not g or g["status"] != "active":
            await interaction.response.send_message("Game already ended.", ephemeral=True); return
        if g["gems_found"] == 0:
            await interaction.response.send_message("Reveal at least one gem before cashing out.", ephemeral=True); return
        profit = round(g["bet"] * g["current_mult"] - g["bet"], 8)
        record_bet(self.user_id, "mines", g["bet"], profit, f"cashout@{g['current_mult']}x")
        with db() as c:
            c.execute("UPDATE mines_games SET status='won' WHERE id=?", (self.game_id,))
            c.commit()
        user = get_user(self.user_id, "")
        embed = discord.Embed(title="💰 Cashed Out!", color=0xFFD700)
        embed.add_field(name="Multiplier", value=f"{g['current_mult']}×", inline=True)
        embed.add_field(name="Profit",     value=f"+{ltc(profit)}",       inline=True)
        embed.add_field(name="Balance",    value=ltc(user["balance"]),    inline=True)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

@bot.tree.command(name="mines", description="Mines — reveal gems, avoid bombs, cash out anytime")
@app_commands.describe(bet="LTC amount", mines=f"Number of mines")
async def mines_cmd(interaction: discord.Interaction, bet: float, mines: int):
    user = get_user(interaction.user.id, str(interaction.user))
    err  = validate_bet(user, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return
    if not MINES_MIN <= mines <= MINES_MAX:
        await interaction.response.send_message(
            f"❌ Mines must be {MINES_MIN}–{MINES_MAX}.", ephemeral=True); return
    with db() as c:
        active = c.execute(
            "SELECT id FROM mines_games WHERE user_id=? AND status='active'",
            (interaction.user.id,)
        ).fetchone()
    if active:
        await interaction.response.send_message(
            "❌ You have an active Mines game. Cash out first.", ephemeral=True); return

    board = ["G"] * (MINES_GRID - mines) + ["M"] * mines
    random.shuffle(board)
    add_balance(interaction.user.id, -bet)
    with db() as c:
        c.execute(
            "INSERT INTO mines_games (user_id,bet,mines,board) VALUES (?,?,?,?)",
            (interaction.user.id, bet, mines, json.dumps(board))
        )
        game_id = c.lastrowid
        c.commit()

    view  = MinesView(game_id, interaction.user.id, board)
    embed = discord.Embed(title="💎 Mines", description="Pick a tile!", color=0x5865F2)
    embed.add_field(name="Bet",   value=ltc(bet),                  inline=True)
    embed.add_field(name="Mines", value=str(mines),                inline=True)
    embed.add_field(name="Gems",  value=str(MINES_GRID - mines),   inline=True)
    await interaction.response.send_message(embed=embed, view=view)

# ── /tictactoe ────────────────────────────────────────────────────────────────
def ttt_winner(board: str):
    for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return "draw" if " " not in board else None

def render_ttt(board: str) -> str:
    m = {"X":"❌","O":"⭕"," ":"⬜"}
    rows = [" ".join(m[board[i+j]] for j in range(3)) for i in range(0,9,3)]
    return "\n".join(rows)

class TicTacToeView(discord.ui.View):
    def __init__(self, game_id: int, challenger: int, opponent: int):
        super().__init__(timeout=TTT_MOVE_TIMEOUT)
        self.game_id    = game_id
        self.challenger = challenger
        self.opponent   = opponent
        for i in range(9):
            btn = discord.ui.Button(label="⬜", row=i//3, custom_id=f"ttt_{game_id}_{i}")
            btn.callback = self._make_cb(i)
            self.add_item(btn)

    def _make_cb(self, idx: int):
        async def cb(interaction: discord.Interaction):
            with db() as c:
                g = c.execute("SELECT * FROM ttt_games WHERE id=?", (self.game_id,)).fetchone()
            if not g or g["status"] != "active":
                await interaction.response.send_message("Game over.", ephemeral=True); return
            if interaction.user.id != g["turn"]:
                await interaction.response.send_message("Not your turn.", ephemeral=True); return
            board = list(g["board"])
            if board[idx] != " ":
                await interaction.response.send_message("Cell taken.", ephemeral=True); return
            sym       = "X" if g["turn"] == self.challenger else "O"
            board[idx]= sym
            board_str = "".join(board)
            next_turn = self.opponent if g["turn"] == self.challenger else self.challenger
            result    = ttt_winner(board_str)
            with db() as c:
                if result:
                    winner_id = None if result == "draw" else (
                        self.challenger if result == "X" else self.opponent)
                    c.execute("UPDATE ttt_games SET board=?,status='done',winner=? WHERE id=?",
                              (board_str, winner_id, self.game_id))
                    c.commit()
                    if result == "draw":
                        add_balance(self.challenger, g["bet"])
                        add_balance(self.opponent,   g["bet"])
                        outcome = "Draw! Both players refunded."
                    else:
                        loser    = self.opponent if winner_id == self.challenger else self.challenger
                        winnings = round(g["bet"] * 2 * (1 - HOUSE_EDGE), 8)
                        add_balance(winner_id, winnings)
                        record_bet(winner_id, "ttt", g["bet"], winnings - g["bet"], "win")
                        record_bet(loser,     "ttt", g["bet"], -g["bet"],           "loss")
                        outcome = f"<@{winner_id}> wins {ltc(winnings)}! 🎉"
                    embed = discord.Embed(title="❌⭕ Tic Tac Toe — Game Over", color=0xFFD700)
                    embed.description = render_ttt(board_str)
                    embed.add_field(name="Result", value=outcome, inline=False)
                    for child in self.children: child.disabled = True
                    await interaction.response.edit_message(embed=embed, view=self)
                else:
                    c.execute("UPDATE ttt_games SET board=?,turn=? WHERE id=?",
                              (board_str, next_turn, self.game_id))
                    c.commit()
                    for child in self.children:
                        if getattr(child, "custom_id", None) == f"ttt_{self.game_id}_{idx}":
                            child.label = sym; child.disabled = True
                            child.style = discord.ButtonStyle.danger if sym == "X" else discord.ButtonStyle.primary
                    embed = discord.Embed(title="❌⭕ Tic Tac Toe", color=0x5865F2)
                    embed.description = render_ttt(board_str)
                    embed.add_field(name="Turn", value=f"<@{next_turn}>",   inline=True)
                    embed.add_field(name="Pot",  value=ltc(g["bet"] * 2),   inline=True)
                    await interaction.response.edit_message(embed=embed, view=self)
        return cb

@bot.tree.command(name="tictactoe", description="1v1 Tic Tac Toe — challenger sets the bet")
@app_commands.describe(opponent="User to challenge", bet="LTC each player puts up")
async def tictactoe(interaction: discord.Interaction, opponent: discord.Member, bet: float):
    if opponent.id == interaction.user.id:
        await interaction.response.send_message("❌ Can't challenge yourself.", ephemeral=True); return
    if opponent.bot:
        await interaction.response.send_message("❌ Can't challenge a bot.", ephemeral=True); return
    challenger = get_user(interaction.user.id, str(interaction.user))
    err = validate_bet(challenger, bet)
    if err: await interaction.response.send_message(f"❌ {err}", ephemeral=True); return

    add_balance(interaction.user.id, -bet)
    with db() as c:
        c.execute("INSERT INTO ttt_games (challenger,opponent,bet,turn) VALUES (?,?,?,?)",
                  (interaction.user.id, opponent.id, bet, interaction.user.id))
        game_id = c.lastrowid
        c.commit()

    class AcceptView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=TTT_ACCEPT_TIMEOUT)

        @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
        async def accept(self, i: discord.Interaction, _btn):
            if i.user.id != opponent.id:
                await i.response.send_message("Not your challenge.", ephemeral=True); return
            opp = get_user(opponent.id, str(opponent))
            e2  = validate_bet(opp, bet)
            if e2:
                add_balance(interaction.user.id, bet)
                with db() as c2:
                    c2.execute("UPDATE ttt_games SET status='cancelled' WHERE id=?", (game_id,))
                    c2.commit()
                await i.response.send_message(f"❌ {e2}", ephemeral=True); return
            add_balance(opponent.id, -bet)
            with db() as c2:
                c2.execute("UPDATE ttt_games SET status='active' WHERE id=?", (game_id,))
                c2.commit()
            self.stop()
            gv    = TicTacToeView(game_id, interaction.user.id, opponent.id)
            embed = discord.Embed(title="❌⭕ Tic Tac Toe", color=0x5865F2)
            embed.description = render_ttt("         ")
            embed.add_field(name="❌ X", value=f"<@{interaction.user.id}>", inline=True)
            embed.add_field(name="⭕ O", value=f"<@{opponent.id}>",         inline=True)
            embed.add_field(name="Pot",  value=ltc(bet * 2),                inline=True)
            embed.add_field(name="Turn", value=f"<@{interaction.user.id}> goes first", inline=False)
            await i.response.edit_message(embed=embed, view=gv)

        @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
        async def decline(self, i: discord.Interaction, _btn):
            if i.user.id not in (opponent.id, interaction.user.id):
                await i.response.send_message("Not your game.", ephemeral=True); return
            add_balance(interaction.user.id, bet)
            with db() as c2:
                c2.execute("UPDATE ttt_games SET status='cancelled' WHERE id=?", (game_id,))
                c2.commit()
            self.stop()
            await i.response.edit_message(content="Challenge declined. Bet refunded.", embed=None, view=None)

    embed = discord.Embed(title="❌⭕ Tic Tac Toe Challenge", color=0xFEE75C)
    embed.add_field(name="Challenger", value=f"<@{interaction.user.id}>", inline=True)
    embed.add_field(name="Opponent",   value=f"<@{opponent.id}>",          inline=True)
    embed.add_field(name="Bet each",   value=ltc(bet),                     inline=True)
    embed.add_field(name="Total Pot",  value=ltc(bet * 2),                 inline=True)
    embed.set_footer(text=f"{opponent.display_name} has {TTT_ACCEPT_TIMEOUT}s to accept.")
    await interaction.response.send_message(embed=embed, view=AcceptView())

# ── /leaderboard ──────────────────────────────────────────────────────────────
@bot.tree.command(name="leaderboard", description="Top 10 balances")
async def leaderboard(interaction: discord.Interaction):
    with db() as c:
        rows = c.execute(
            "SELECT username,balance,games_played FROM users ORDER BY balance DESC LIMIT 10"
        ).fetchall()
    medals = ["🥇","🥈","🥉"] + ["🎖️"] * 7
    embed  = discord.Embed(title="🏆 Leaderboard", color=0xFFD700)
    for i, r in enumerate(rows):
        embed.add_field(
            name=f"{medals[i]} {r['username']}",
            value=f"{ltc(r['balance'])} ({r['games_played']} games)",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

# ── /history ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="history", description="Your last 10 bets")
async def history(interaction: discord.Interaction):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM bets WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (interaction.user.id,)
        ).fetchall()
    if not rows:
        await interaction.response.send_message("No bet history yet.", ephemeral=True); return
    embed = discord.Embed(title="📜 Bet History", color=0x5865F2)
    for r in rows:
        sign = "+" if r["profit"] >= 0 else ""
        embed.add_field(
            name=f"{r['game'].upper()} — {r['timestamp'][:16]}",
            value=f"Bet: {ltc(r['bet'])} | P/L: {sign}{ltc(r['profit'])}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /help ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name="help", description="All commands")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎰 LTC Gambling Bot",
        description="Real LTC gambling. Deposit, play, withdraw.",
        color=0x345D9D
    )
    cmds = [
        ("💳 /deposit",                         "Your unique LTC deposit address"),
        ("📤 /withdraw [amount] [address]",      "Withdraw LTC to external wallet"),
        ("💰 /balance",                          "Your balance & stats"),
        ("🎲 /dice [bet] [target] [over|under]", f"Dice — target {DICE_MIN_TARGET}–{DICE_MAX_TARGET}"),
        ("🪙 /coinflip [bet] [heads|tails]",     f"50/50 flip — {round((1-HOUSE_EDGE)*2,4)}× payout"),
        ("🚀 /crash [bet] [cashout]",            f"Crash — min cashout {CRASH_MIN_CASHOUT}×"),
        ("🌀 /limbo [bet] [target]",             f"Limbo — {LIMBO_MIN_TARGET}×–{LIMBO_MAX_TARGET}×"),
        ("💎 /mines [bet] [mines]",              f"Mines — {MINES_MIN}–{MINES_MAX} mines on {MINES_GRID} tiles"),
        ("🎰 /slots [bet]",                      f"Slots — up to {SLOTS_CLOVER}× jackpot"),
        ("❌⭕ /tictactoe [@user] [bet]",         "1v1 Tic Tac Toe, winner takes pot"),
        ("📜 /history",                          "Last 10 bets"),
        ("🏆 /leaderboard",                      "Top 10 balances"),
    ]
    for name, desc in cmds:
        embed.add_field(name=name, value=desc, inline=False)
    embed.set_footer(
        text=f"House edge: {HOUSE_EDGE*100}%  |  Min bet: {ltc(MIN_BET)}  |  Min deposit: {ltc(MIN_DEPOSIT)}"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    bot.run(DISCORD_TOKEN)
