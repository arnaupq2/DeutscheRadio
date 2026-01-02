
import discord
from discord.ext import commands
import asyncio
import os
import yt_dlp
import edge_tts
import feedparser
import random
import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from keep_alive import keep_alive
import aiohttp
from dotenv import load_dotenv
import math

load_dotenv(override=True)

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER = "arnaupq"
CITIES = ["Berlin", "Wiesbaden", "Munchen", "Hamburg", "Palma de Mallorca"]
NEWS_FEED = "https://www.rbb24.de/aktuell/index.xml/feed=rss.xml"
DEUTSCHLAND_FILE = "deutschland.m4a"

# Lista de proxies para rotación en caso de fallo (vacío por defecto)
PROXIES = [
    # "http://user:pass@1.2.3.4:8080",
]

# ---------------- COOKIES SETUP (ENV VAR) ----------------
# Si existe la variable de entorno COOKIES_CONTENT, creamos el archivo cookies.txt al vuelo.
# Esto es para mantener el secreto en Render sin subir el archivo.
cookies_content = os.getenv("COOKIES_CONTENT")
if cookies_content:
    with open("cookies.txt", "w") as f:
        f.write(cookies_content)
    print("🍪 cookies.txt creado desde variable de entorno.")
elif os.path.exists("cookies.txt"):
    print("🍪 cookies.txt encontrado en el sistema (Secret File o local).")
else:
    print("⚠️ NO SE ENCONTRÓ COOKIES.TXT (Ni variable ni archivo).")

# DEBUG EXTRA: Confirmar ruta y tamaño para Render
abs_cookie_path = os.path.abspath("cookies.txt")
if os.path.exists(abs_cookie_path):
    print(f"📊 DEBUG PATH: {abs_cookie_path}")
    print(f"📊 DEBUG SIZE: {os.path.getsize(abs_cookie_path)} bytes")
else:
    print(f"❌ DEBUG: El archivo NO existe en {abs_cookie_path}")

# Configuración YTDL (Modo Android + Cookies)
YTDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False, # <--- DEBUG: Activado logs
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': os.path.abspath('cookies.txt'), 
    'force_ipv4': True,
    'extractor_args': {'youtube': {'player_client': ['android', 'ios']}}, 
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': True, # <--- DEBUG: Logs a stderr
    'no_warnings': False, # <--- DEBUG: Ver warnings
}

# (Omitted Middle Sections - Keeping API helpers as they are useful fallbacks if cookies fail or for speed)

# ---------------- PLAYBACK LOGIC ----------------
async def play_next(ctx_or_vc):
    vc = state.voice_client
    if not vc or not vc.is_connected(): return

    if state.next_tts_message:
        text, state.next_tts_message = state.next_tts_message, None
        tts_file = await generate_tts(text)
        vc.play(discord.FFmpegPCMAudio(tts_file), after=lambda e: bot.loop.create_task(play_next(ctx_or_vc)))
        return

    if state.song_counter > 0 and state.song_counter % 7 == 0:
        state.song_counter += 1
        weather = await get_weather_text()
        news = await get_berlin_news()
        full_text = f"Das Wetter. {weather}. Und nun die Nachrichten. {news}. Weiter geht es mit Musik."
        state.next_tts_message = full_text
        await play_next(ctx_or_vc)
        return

    song_url = state.get_next_song()
    if not song_url:
        await asyncio.sleep(10); await play_next(ctx_or_vc); return

    print(f"🔍 Procesando: {song_url}")
    stream_url = None
    title = "Radio Stream"

    # STRATEGY 1: COBALT (Prioridad por rapidez)
    if not stream_url and ("youtube.com" in song_url or "youtu.be" in song_url):
        stream_url = await get_stream_from_cobalt(song_url)
        if stream_url: title = "Radio Play (Cobalt)"

    # STRATEGY 2: LOCAL YTDL + COOKIES (La más fiable con login)
    if not stream_url:
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(YTDL_OPTS).extract_info(song_url, download=False))
            if 'entries' in data: data = data['entries'][0]
            stream_url = data['url']; title = data.get('title', 'Unknown')
        except Exception as e:
            print(f"❌ YTDL (Cookies) falló: {e}")

    # STRATEGY 3: FALLBACK APIs (Piped/Invidious)
    if not stream_url:
        vid = extract_video_id(song_url)
        if vid:
            stream_url = await get_stream_from_piped(vid) or await get_stream_from_invidious(vid)
            if stream_url: title = "Radio Play (API Fallback)"

    if stream_url:
        print(f"▶️ Reproduciendo: {title}")
        state.song_counter += 1
        SAFE_FFMPEG = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
        source = discord.FFmpegPCMAudio(stream_url, **SAFE_FFMPEG)
        vc.play(source, after=lambda e: bot.loop.create_task(play_next(ctx_or_vc)))
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=title))
    else:
        print("❌ Imposible reproducir canción. Saltando...")
        state.song_counter += 1
        await asyncio.sleep(5)
        await play_next(ctx_or_vc)
FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=":", intents=intents)

# ---------------- STATE ----------------
class RadioState:
    def __init__(self):
        self.queue = []
        self.song_counter = 0
        self.voice_client = None
        self.playlist_file = "lista_canciones.txt"
        self.load_playlist()
        self.next_tts_message = None
        self.active_vote = False

    def load_playlist(self):
        if not os.path.exists(self.playlist_file):
            with open(self.playlist_file, "w") as f: f.write("")
        with open(self.playlist_file, "r") as f:
            self.permanent_playlist = [l.strip() for l in f.readlines() if l.strip()]

    def add_to_playlist(self, song):
        self.permanent_playlist.append(song)
        with open(self.playlist_file, "a") as f:
            f.write(f"\n{song}")
            
    def remove_from_playlist(self, query):
        initial_len = len(self.permanent_playlist)
        self.permanent_playlist = [s for s in self.permanent_playlist if query.lower() not in s.lower()]
        with open(self.playlist_file, "w") as f:
            for s in self.permanent_playlist:
                f.write(f"{s}\n")
        return initial_len - len(self.permanent_playlist)

    def get_next_song(self):
        if self.queue:
            state.active_vote = False # Reset vote if we are playing from queue
            self.last_played = self.queue.pop(0)
            return self.last_played
            
        if self.permanent_playlist:
            choices = self.permanent_playlist
            # Avoid repeating last song if possible
            if len(choices) > 1 and hasattr(self, 'last_played') and self.last_played in choices:
                 choices = [c for c in choices if c != self.last_played]
            
            self.last_played = random.choice(choices)
            return self.last_played
        return None

state = RadioState()
scheduler = AsyncIOScheduler()

# ---------------- HELPERS ----------------
async def get_weather_text():
    reports = []
    async with aiohttp.ClientSession() as session:
        for city in CITIES:
            try:
                url = f"https://wttr.in/{city}?format=%t+%C"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        cleaned = text.strip()
                        reports.append(f"{city}: {cleaned}")
            except: continue
    return " . ".join(reports)

async def get_berlin_news():
    try:
        feed = feedparser.parse(NEWS_FEED)
        if feed.entries:
            entry = feed.entries[0]
            clean_desc = entry.description.split('<')[0]
            return f"Nachrichten aus Berlin: {entry.title}. {clean_desc[:200]}"
    except: return ""
    return "Keine aktuellen Nachrichten."

async def generate_tts(text, filename="tts_temp.mp3"):
    communicate = edge_tts.Communicate(text, "de-DE-ConradNeural")
    await communicate.save(filename)
    return filename

# ---------------- STREAM EXTRACTORS ----------------

async def get_stream_from_invidious(video_id):
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.jing.rocks",
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de"
    ]
    async with aiohttp.ClientSession() as session:
        for base_url in instances:
            try:
                print(f"🔄 Invidious API: {base_url}")
                url = f"{base_url}/api/v1/videos/{video_id}"
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "formatStreams" in data:
                            # Prefer m4a/mp4 per compatibility
                            formats = data["formatStreams"]
                            best = sorted(formats, key=lambda x: x.get("bitrate", "0") or 0, reverse=True)[0]
                            return best["url"]
            except: continue
    return None

# ---------------- STREAM EXTRACTORS ----------------
async def get_stream_from_cobalt(url):
    api_url = "https://api.cobalt.tools/api/json"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {"url": url, "isAudioOnly": True}
    
    async with aiohttp.ClientSession() as session:
        try:
            print(f"🔄 Cobalt API: {url}")
            async with session.post(api_url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "url" in data: return data["url"]
                    elif "picker" in data:
                        for item in data["picker"]:
                            if item.get("type") == "audio": return item["url"]
        except Exception as e: print(f"Cobalt error: {e}")
    return None

async def get_stream_from_piped(video_id):
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.leptons.xyz",
        "https://api.piped.privacy.com.de"
    ]
    async with aiohttp.ClientSession() as session:
        for base_url in instances:
            try:
                print(f"🔄 Piped API: {base_url}")
                url = f"{base_url}/streams/{video_id}"
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        audio_streams = data.get("audioStreams", [])
                        if not audio_streams: continue
                        best_audio = sorted(audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
                        return best_audio["url"]
            except: continue
    return None

def extract_video_id(url):
    if "v=" in url: return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: return url.split("youtu.be/")[1].split("?")[0]
    return None

# ---------------- PLAYBACK LOGIC ----------------
async def play_next(ctx_or_vc):
    vc = state.voice_client
    if not vc or not vc.is_connected(): return

    if state.next_tts_message:
        text, state.next_tts_message = state.next_tts_message, None
        tts_file = await generate_tts(text)
        vc.play(discord.FFmpegPCMAudio(tts_file), after=lambda e: bot.loop.create_task(play_next(ctx_or_vc)))
        return

    if state.song_counter > 0 and state.song_counter % 7 == 0:
        state.song_counter += 1
        weather = await get_weather_text()
        news = await get_berlin_news()
        full_text = f"Das Wetter. {weather}. Und nun die Nachrichten. {news}. Weiter geht es mit Musik."
        state.next_tts_message = full_text
        await play_next(ctx_or_vc)
        return

    song_url = state.get_next_song()
    if not song_url:
        await asyncio.sleep(10); await play_next(ctx_or_vc); return

    print(f"🔍 Procesando: {song_url}")
    stream_url = None
    title = "Radio Stream"

    # STRATEGY 1: COBALT
    if not stream_url and ("youtube.com" in song_url or "youtu.be" in song_url):
        stream_url = await get_stream_from_cobalt(song_url)
        if stream_url: title = "Radio Play (Cobalt)"

    # STRATEGY 2: INVIDIOUS (Agregado)
    if not stream_url:
        vid = extract_video_id(song_url)
        if vid:
            stream_url = await get_stream_from_invidious(vid)
            if stream_url: title = "Radio Play (Invidious)"

    # STRATEGY 3: PIPED
    if not stream_url:
        vid = extract_video_id(song_url)
        if vid:
            stream_url = await get_stream_from_piped(vid)
            if stream_url: title = "Radio Play (Piped)"

    # STRATEGY 4: LOCAL YTDL + PROXY ROTATION (Fallback principal con cookies)
    if not stream_url:
        print("⚠️ APIs fallaron. Usando YTDL con Cookies...")
        loop = asyncio.get_event_loop()
        
        # Try without proxy first
        try:
            print(f"🕵️ Intentando extraer info con YTDL para: {song_url}")
            data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(YTDL_OPTS).extract_info(song_url, download=False))
            if 'entries' in data: data = data['entries'][0]
            stream_url = data['url']; title = data.get('title', 'Unknown')
            print(f"✅ YTDL Éxito: {title} | URL: {stream_url[:40]}...")
        except Exception as e:
            print(f"❌ YTDL Error Crítico: {e}")
            # Try with proxies
            for proxy in PROXIES:
                print(f"trying proxy: {proxy}")
                PROXY_OPTS = YTDL_OPTS.copy()
                PROXY_OPTS['proxy'] = proxy
                try:
                    data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(PROXY_OPTS).extract_info(song_url, download=False))
                    if 'entries' in data: data = data['entries'][0]
                    stream_url = data['url']; title = data.get('title', 'Unknown')
                    print("✅ Proxy funcionó!")
                    break
                except: continue

    if stream_url:
        print(f"▶️ Reproduciendo: {title}")
        print(f"🔗 Link: {stream_url[:50]}...")
        state.song_counter += 1
        SAFE_FFMPEG = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
        source = discord.FFmpegPCMAudio(stream_url, **SAFE_FFMPEG)
        vc.play(source, after=lambda e: bot.loop.create_task(play_next(ctx_or_vc)))
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=title))
        
        await asyncio.sleep(2)
        if not vc.is_playing():
            print("⚠️ Silencio detectado. Saltando...")
            vc.stop()
    else:
        print("❌ Todo falló. Saltando canción...")
        state.song_counter += 1
        await asyncio.sleep(5)
        await play_next(ctx_or_vc)

# ---------------- COMMANDS (GERMAN) ----------------
@bot.command(name="deutschland")
async def cmd_deutschland(ctx):
    if ctx.author.name != ADMIN_USER: return
    await daily_deutschland()

@bot.command(name="join")
async def cmd_join(ctx):
    if ctx.author.voice:
        try:
            # Aumentamos timeout a 60s y forzamos reconexión para entornos lentos (Render)
            state.voice_client = await ctx.author.voice.channel.connect(timeout=60, reconnect=True, self_deaf=True)
            await ctx.send(f"📻 Verbunden mit **{ctx.author.voice.channel.name}**")
            await play_next(ctx)
        except asyncio.TimeoutError:
            await ctx.send("❌ Error: Tiempo de espera agotado al conectar. Discord está lento o bloqueando la conexión UDP.")
        except Exception as e:
            await ctx.send(f"❌ Error al conectar: {e}")
    else:
        await ctx.send("⚠️ Du musst erst einem Sprachkanal beitreten.")

@bot.command(name="addsong")
async def cmd_addsong(ctx, *, query: str):
    if ctx.author.name != ADMIN_USER: return await ctx.send("⛔ Zugriff verweigert.")
    state.add_to_playlist(query)
    await ctx.send(f"✅ Hinzugefügt: {query}")

@bot.command(name="list")
async def cmd_list(ctx):
    if ctx.author.name != ADMIN_USER: return await ctx.send("⛔ Zugriff verweigert.")
    if not state.permanent_playlist:
        return await ctx.send("📂 Die Wiedergabeliste ist leer.")
    
    # Send as file if too long
    content = "\n".join(state.permanent_playlist)
    if len(content) > 1900:
        with open("temp_list.txt", "w") as f: f.write(content)
        await ctx.send("📂 Wiedergabeliste:", file=discord.File("temp_list.txt"))
        os.remove("temp_list.txt")
    else:
        await ctx.send(f"📂 **Wiedergabeliste**:\n```{content}```")

@bot.command(name="delete")
async def cmd_delete(ctx, *, query: str):
    if ctx.author.name != ADMIN_USER: return await ctx.send("⛔ Zugriff verweigert.")
    removed = state.remove_from_playlist(query)
    if removed > 0:
        await ctx.send(f"🗑️ {removed} Song(s) entfernt, die '{query}' enthielten.")
    else:
        await ctx.send(f"⚠️ Keine Songs gefunden für '{query}'.")

@bot.command(name="skip")
async def cmd_skip(ctx):
    if state.active_vote: return await ctx.send("⚠️ Abstimmung läuft bereits.")
    
    vc = state.voice_client
    if not vc or not vc.is_playing() or not ctx.author.voice or ctx.author.voice.channel != vc.channel:
        return await ctx.send("⚠️ Fehler: Bot spielt nicht oder falscher Kanal.")

    # Admin Force Skip
    if ctx.author.name == ADMIN_USER:
        vc.stop()
        return await ctx.send("⏭️ (Admin) Übersprungen.")

    # User Vote Skip
    members = [m for m in vc.channel.members if not m.bot]
    required_votes = math.ceil(len(members) / 2)
    
    if len(members) < 2:
        vc.stop()
        return await ctx.send("⏭️ Übersprungen.")

    state.active_vote = True
    msg = await ctx.send(f"🗳️ Skip Vote! Benötigt: **{required_votes}**\nReagiere mit ⏭️ (60s).")
    await msg.add_reaction("⏭️")

    try:
        await asyncio.sleep(60)
        cache_msg = await ctx.channel.fetch_message(msg.id)
        reaction = discord.utils.get(cache_msg.reactions, emoji="⏭️")
        count = reaction.count - 1 if reaction else 0
        state.active_vote = False
        
        if count >= required_votes:
            vc.stop()
            await ctx.send(f"✅ Skip erfolgreich ({count}/{required_votes}).")
        else:
            await ctx.send(f"❌ Skip gescheitert ({count}/{required_votes}).")
    except:
        state.active_vote = False

@bot.command(name="coment")
async def cmd_coment(ctx):
    if ctx.author.name != ADMIN_USER: return await ctx.send("⛔ Zugriff verweigert.")
    await ctx.send("🔄 Erstelle Nachrichten...")
    weather = await get_weather_text()
    news = await get_berlin_news()
    full_text = f"Das Wetter. {weather}. Und nun die Nachrichten. {news}. Weiter geht es mit Musik."
    state.next_tts_message = full_text
    
    if state.voice_client and state.voice_client.is_playing(): state.voice_client.stop()
    elif state.voice_client: await play_next(ctx)
    await ctx.send("🎙️ Spezialsendung in Kürze.")

# ---------------- VOTING SYSTEM ----------------
@bot.command(name="play")
async def cmd_play(ctx, *, url: str):
    if state.active_vote:
        return await ctx.send("⚠️ Eine Abstimmung läuft bereits.")
    
    vc = state.voice_client
    if not vc or not ctx.author.voice or ctx.author.voice.channel != vc.channel:
        return await ctx.send("⚠️ Du musst im gleichen Sprachkanal sein.")
        
    members = [m for m in vc.channel.members if not m.bot]
    total_members = len(members)
    
    # If few people, add directly
    if total_members < 2:
        state.queue.insert(0, url)
        if not vc.is_playing(): await play_next(ctx)
        return await ctx.send(f"✅ Akzeptiert: {url}")

    # Voting required
    required_votes = math.ceil(total_members / 2)
    state.active_vote = True
    
    msg = await ctx.send(
        f"🗳️ **Abstimmung für neuen Song!**\n{url}\n"
        f"Benötigte Stimmen: **{required_votes}**\n"
        "Reagiere mit 👍 um zuzustimmen (60s)."
    )
    await msg.add_reaction("👍")
    
    def check(reaction, user):
        return user in members and str(reaction.emoji) == "👍" and reaction.message.id == msg.id

    try:
        # Wait until enough unique users react (minus bot)
        # Simplified: Just wait 60s and count
        await asyncio.sleep(60)
        
        # Refresh message to count reactions
        cache_msg = await ctx.channel.fetch_message(msg.id)
        reaction = discord.utils.get(cache_msg.reactions, emoji="👍")
        count = reaction.count - 1 if reaction else 0 # Subtract bot
        
        state.active_vote = False
        
        if count >= required_votes:
            state.queue.insert(0, url)
            await ctx.send(f"✅ Abstimmung erfolgreich ({count}/{required_votes})! Song hinzugefügt.")
            if not vc.is_playing(): await play_next(ctx)
        else:
            await ctx.send(f"❌ Abstimmung gescheitert ({count}/{required_votes}).")
            
    except Exception as e:
        state.active_vote = False
        print(f"Vote Error: {e}")

# ---------------- SCHEDULER ----------------
async def daily_deutschland():
    print("🇩🇪 ZEIT FÜR DEUTSCHLAND")
    vc = state.voice_client
    
    # Auto-Connect Logic
    if not vc or not vc.is_connected():
        guild = bot.guilds[0] if bot.guilds else None
        if guild:
            # 1. Admin, 2. Populated, 3. First
            member = guild.get_member_named(ADMIN_USER)
            target = member.voice.channel if member and member.voice else None
            if not target: target = max(guild.voice_channels, key=lambda c: len(c.members), default=None)
            if not target and guild.voice_channels: target = guild.voice_channels[0]
            
            if target:
                try: state.voice_client = await target.connect(); vc = state.voice_client
                except: pass

    # Strict Play
    if vc:
        if vc.is_playing(): vc.stop()
        if os.path.exists(DEUTSCHLAND_FILE):
             vc.play(discord.FFmpegPCMAudio(DEUTSCHLAND_FILE), 
                   after=lambda e: bot.loop.create_task(play_next(None)))
        else:
            print(f"⚠️ {DEUTSCHLAND_FILE} nicht gefunden!")
            bot.loop.create_task(play_next(None))

async def connection_monitor():
    await bot.wait_until_ready()
    while not bot.is_closed():
        # Keep alive logic here
        await asyncio.sleep(60)

@bot.event
async def on_ready():
    print(f'✅ Eingeloggt als {bot.user}')
    keep_alive()
    if not scheduler.running:
        # 00:00 CET = Europe/Berlin
        scheduler.add_job(daily_deutschland, CronTrigger(hour=0, minute=0, timezone="Europe/Berlin"))
        scheduler.start()
    bot.loop.create_task(connection_monitor())

bot.run(TOKEN)