"""vc_joiner.py — public + private VC join, silent listener, stays in VC."""
import asyncio, uuid, os, wave, logging, time

import pyrogram.errors as _pg_errors
import pyrogram.raw.functions.phone as _phone_module
from pyrogram.raw.core import TLObject


class _DummyGroupCallError(_pg_errors.BadRequest):
    pass


_MISSING_ERRORS = [
    "GroupcallForbidden", "GroupCallForbidden",
    "GroupcallInvalid", "GroupCallInvalid",
    "GroupcallNotFound", "GroupCallNotFound",
    "GroupcallAlreadyDiscarded", "GroupCallAlreadyDiscarded",
    "GroupcallJoinMissing", "GroupCallJoinMissing",
    "GroupcallNotModified", "GroupCallNotModified",
    "GroupcallParticipantNotFound", "GroupCallParticipantNotFound",
    "GroupcallAlreadyJoined", "GroupCallAlreadyJoined",
]
for _err_name in _MISSING_ERRORS:
    if not hasattr(_pg_errors, _err_name):
        setattr(_pg_errors, _err_name,
                type(_err_name, (_DummyGroupCallError,), {}))

try:
    import pyrogram.errors.exceptions.bad_request_400 as _b400
    for _n in _MISSING_ERRORS:
        if not hasattr(_b400, _n):
            setattr(_b400, _n, getattr(_pg_errors, _n))
except Exception:
    pass

try:
    import pyrogram.errors.exceptions.forbidden_403 as _f403
    for _n in _MISSING_ERRORS:
        if not hasattr(_f403, _n):
            setattr(_f403, _n, getattr(_pg_errors, _n))
except Exception:
    pass


class _StubTLObject(TLObject):
    ID = 0
    def __init__(self, *a, **k): pass
    def write(self): return b""
    @classmethod
    def read(cls, *a, **k): return None


_MISSING_RAW = {
    "GetGroupCallChainBlocks": 0xeeee22cc,
    "CreateConferenceCall": 0x7d0444bb,
    "GetGroupCallStreamChannels": 0x1ab21940,
    "GetGroupCallStreamRtmpUrl": 0xc5c1c8f4,
    "SendGroupCallMessage": 0xee1f9b1f,
}
for _name, _id in _MISSING_RAW.items():
    if not hasattr(_phone_module, _name):
        setattr(_phone_module, _name,
                type(_name, (_StubTLObject,), {"ID": _id}))


from pyrogram import Client
from pyrogram.errors import UserAlreadyParticipant, FloodWait
from pyrogram.raw import functions
from pyrogram.raw.types import InputGroupCall
from config import API_ID, API_HASH, SILENT_WAV
from database import (
    all_accounts, mark_joined, has_joined,
    get_cached_chat_id, cache_chat_id,
    get_flood_until, set_flood_until,
)

logger = logging.getLogger(__name__)
logger.info("Pyrogram GroupCall patches applied")

PyTgCalls = None
MediaStream = None
PYTGCALLS_OK = False
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
    PYTGCALLS_OK = True
    logger.info("PyTgCalls loaded")
except Exception as e:
    logger.error(f"PyTgCalls unavailable: {e}")

_active_sessions = {}
_stop_events = {}
_user_stop = {}

VC_BATCH_SIZE = 5
VC_BATCH_DELAY = 3
MAX_DEAD_TICKS = 20   # 20 * 15s = 5 min before leaving — very conservative

DEAD_MARKERS = (
    "USER_DEACTIVATED", "SESSION_REVOKED", "AUTH_KEY_UNREGISTERED",
    "USER_DEACTIVATED_BAN", "PHONE_NUMBER_BANNED", "USER_BANNED",
)

# ── Infinite silent stream ──
SILENT_PIPE = "/tmp/vc_silent_pipe"
_pipe_writer = None


def ensure_silent():
    global _pipe_writer
    try:
        if not os.path.exists(SILENT_PIPE):
            os.mkfifo(SILENT_PIPE)
        if _pipe_writer is None or _pipe_writer.done():
            _pipe_writer = asyncio.create_task(_silent_pump())
        logger.info(f"silent pipe ready: {SILENT_PIPE}")
    except Exception as e:
        logger.error(f"silent pipe setup failed: {e}")


async def _silent_pump():
    """Write silence to the FIFO without blocking the asyncio event loop."""
    chunk = b"\x00\x00" * 44100
    while True:
        fd = None
        try:
            # O_NONBLOCK prevents open() hanging until FFmpeg opens the reader.
            fd = os.open(SILENT_PIPE, os.O_WRONLY | os.O_NONBLOCK)
            view = memoryview(chunk)
            while view:
                try:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise BrokenPipeError("FIFO closed by reader")
                    view = view[written:]
                except BlockingIOError:
                    await asyncio.sleep(0.05)
                except BrokenPipeError:
                    break
            await asyncio.sleep(0.9)
        except OSError as e:
            logger.debug(f"silent pump reconnecting: {e}")
            await asyncio.sleep(1)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass



def _is_private_link(link):
    s = link.strip().lower()
    return "/+" in s or "joinchat/" in s


async def is_vc_live(app, chat_id):
    """
    Try THREE methods to detect live VC:
    1. GetFullChannel.call — works for public channels
    2. GetFullChat.call — works for some megagroups
    3. GetGroupCall raw invocation — works when peer+call is cached
    """
    # Method 1: GetFullChannel
    try:
        peer = await app.resolve_peer(chat_id)
        try:
            full = await app.invoke(
                functions.channels.GetFullChannel(channel=peer))
            fc = getattr(full, "full_chat", None)
            if fc is not None:
                call = getattr(fc, "call", None)
                if call is not None:
                    logger.debug(f"is_vc_live[{chat_id}]: GetFullChannel.call ✓")
                    return True
        except Exception as e:
            logger.debug(f"is_vc_live GetFullChannel: {e}")

        # Method 2: GetFullChat
        try:
            full = await app.invoke(
                functions.messages.GetFullChat(chat_id=chat_id))
            fc = getattr(full, "full_chat", None)
            if fc is not None:
                call = getattr(fc, "call", None)
                if call is not None:
                    logger.debug(f"is_vc_live[{chat_id}]: GetFullChat.call ✓")
                    return True
        except Exception as e:
            logger.debug(f"is_vc_live GetFullChat: {e}")

        # Method 3: raw GetGroupCall — if VC exists, this returns a GroupCall
        try:
            from pyrogram.raw.functions.phone import GetGroupCall
            full = await app.invoke(
                functions.channels.GetFullChannel(channel=peer))
            fc = getattr(full, "full_chat", None)
            call_obj = getattr(fc, "call", None) if fc else None
            if call_obj is not None and hasattr(call_obj, "id"):
                # Try to fetch the group call
                try:
                    await app.invoke(GetGroupCall(
                        call=InputGroupCall(id=call_obj.id,
                                            access_hash=call_obj.access_hash),
                        limit=1))
                    logger.debug(f"is_vc_live[{chat_id}]: GetGroupCall ✓")
                    return True
                except Exception as e:
                    err = str(e)
                    if "GROUPCALL_NOT_FOUND" in err or "GROUPCALL_INVALID" in err:
                        return False
                    # Other errors → assume live (better to stay)
                    logger.debug(f"GetGroupCall err (assuming live): {e}")
                    return True
        except Exception as e:
            logger.debug(f"is_vc_live GetGroupCall: {e}")
    except Exception as e:
        logger.debug(f"is_vc_live outer: {e}")

    return False


async def _drop_dead(phone, err=""):
    if not any(m in err for m in DEAD_MARKERS):
        return
    from database import accounts
    try:
        await accounts.delete_one({"phone": phone})
        logger.warning(f"Dropped {phone} - {err[:80]}")
    except Exception:
        pass


async def _resolve_and_join(app, chat_target, link, phone):
    flood_until = await get_flood_until(phone)
    if flood_until > time.time():
        logger.info(f"   {phone} flood {int(flood_until-time.time())}s")
        return None

    is_private = _is_private_link(link)

    cached_id, _ = await get_cached_chat_id(link)
    if cached_id:
        if is_private and not await has_joined(link, phone):
            try:
                await app.join_chat(chat_target)
            except UserAlreadyParticipant:
                pass
            except FloodWait as fw:
                await set_flood_until(phone, time.time() + fw.value)
                logger.warning(f"   {phone} FLOOD_WAIT {fw.value}s")
                return None
            except Exception:
                pass
            await mark_joined(link, phone)
        return cached_id

    try:
        chat = await app.get_chat(chat_target)
        cid = chat.id
        await cache_chat_id(link, cid, getattr(chat, "title", ""))
        if is_private:
            await mark_joined(link, phone)
        return cid
    except Exception as e:
        err = str(e)
        if any(m in err for m in DEAD_MARKERS):
            await _drop_dead(phone, err)
            return None

    try:
        await app.join_chat(chat_target)
    except UserAlreadyParticipant:
        pass
    except FloodWait as fw:
        await set_flood_until(phone, time.time() + fw.value)
        logger.warning(f"   {phone} FLOOD_WAIT {fw.value}s")
        return None
    except Exception as e:
        err = str(e)
        if any(m in err for m in DEAD_MARKERS):
            await _drop_dead(phone, err)
            return None

    try:
        chat = await app.get_chat(chat_target)
        cid = chat.id
        await cache_chat_id(link, cid, getattr(chat, "title", ""))
        await mark_joined(link, phone)
        return cid
    except Exception:
        return None


async def _play_infinite(call_py, chat_id):
    try:
        stream = MediaStream(SILENT_PIPE)
    except Exception as e:
        logger.error(f"stream build: {e}")
        return False
    try:
        await call_py.play(chat_id, stream)
        try:
            await call_py.unmute(chat_id)
        except Exception:
            pass
        return True
    except Exception as e:
        err = str(e)
        # A failed play() call is not proof that the account joined the call.
        # Do not report listener-mode success for permission/peer errors.
        logger.error(f"PyTgCalls play/join failed for {chat_id}: {err[:200]}")
        return False


async def _join_one(phone, session, chat_target, link="", key=""):
    app = None
    try:
        app = Client(
            f"vcj_{uuid.uuid4().hex[:6]}",
            api_id=API_ID, api_hash=API_HASH,
            session_string=session,
            in_memory=True, no_updates=True,
        )
        await app.start()

        chat_id = await _resolve_and_join(app, chat_target,
                                          link or chat_target, phone)
        if chat_id is None:
            try:
                await app.stop()
            except Exception:
                pass
            return None

        try:
            await app.resolve_peer(chat_id)
        except Exception:
            pass

        call_py = PyTgCalls(app)
        await call_py.start()
        played = await _play_infinite(call_py, chat_id)
        if not played:
            try:
                await call_py.leave_call(chat_id)
            except Exception:
                pass
            try:
                await app.stop()
            except Exception:
                pass
            logger.warning(
                f"{phone}: VC join was not confirmed for {chat_target}; "
                "not marking this session as joined."
            )
            return None

        _active_sessions.setdefault(key, {})[phone] = {
            "client": app,
            "call": call_py,
            "chat_id": chat_id,
            "target": chat_target,
        }
        logger.info(f"{phone} joined VC {chat_target}: {chat_id}")
        return chat_id
    except Exception as e:
        err = str(e)
        if any(m in err for m in DEAD_MARKERS):
            await _drop_dead(phone, err)
        else:
            logger.error(f"VC join {phone}: {err[:120]}")
        if app:
            try:
                await app.stop()
            except Exception:
                pass
        return None


async def leave_one(phone, key=""):
    bucket = _active_sessions.get(key, {})
    blk = bucket.pop(phone, None)
    if not blk:
        return
    try:
        await blk["call"].leave_call(blk["chat_id"])
    except Exception:
        pass
    try:
        await blk["client"].stop()
    except Exception:
        pass
    logger.info(f"{phone} left VC {blk.get('target','')}")


async def leave_all(key=""):
    if key:
        bucket = _active_sessions.get(key, {})
        phones = list(bucket.keys())
        for i in range(0, len(phones), 5):
            batch = phones[i:i + 5]
            await asyncio.gather(*[leave_one(ph, key) for ph in batch],
                                 return_exceptions=True)
            if i + 5 < len(phones):
                await asyncio.sleep(1.0)
        _active_sessions.pop(key, None)
    else:
        for k in list(_active_sessions.keys()):
            bucket = _active_sessions[k]
            phones = list(bucket.keys())
            for i in range(0, len(phones), 5):
                batch = phones[i:i + 5]
                await asyncio.gather(*[leave_one(ph, k) for ph in batch],
                                     return_exceptions=True)
                if i + 5 < len(phones):
                    await asyncio.sleep(1.0)
            _active_sessions.pop(k, None)


async def _join_batch(accs, chat_target, link, stop_event, key):
    bucket = _active_sessions.setdefault(key, {})
    tasks = []
    for acc in accs:
        if stop_event.is_set():
            break
        phone = acc["phone"]
        if phone in bucket:
            continue
        tasks.append(asyncio.create_task(
            _join_one(phone, acc["session"], chat_target, link, key)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if isinstance(r, int))
    return ok, len(tasks) - ok


async def auto_vc_worker(key, chat_target, stop_event,
                         max_accounts=None, is_private=False,
                         owner_uid=0):
    """Disabled: do not fan out voice-chat joins across stored sessions."""
    logger.warning(
        "Automated multi-account VC joining is disabled in this safe build."
    )
    return


def stop_worker(key):
    ev = _stop_events.get(key)
    if ev:
        ev.set()


def request_user_stop(owner_uid):
    _user_stop[owner_uid] = True
    logger.info(f"🛑 User {owner_uid} requested VC stop")
    for key, ev in list(_stop_events.items()):
        ev.set()


def clear_user_stop(owner_uid):
    _user_stop.pop(owner_uid, None)


def is_user_stopped(owner_uid):
    return bool(_user_stop.get(owner_uid))


def active_sessions(key=None):
    if key:
        return dict(_active_sessions.get(key, {}))
    total = {}
    for bucket in _active_sessions.values():
        total.update(bucket)
    return total