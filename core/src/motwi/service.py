"""The long-lived Motwi supervisor behind the app window and the local API.

It owns the store, the live config and one event bus for the whole process. The Twitch
session (EventSub in, Helix out, and the engine between them) can stop and start again
without losing any of that: after a sign-in, a reconnect, or finishing setup.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx

from motwi import __version__
from motwi.ai.client import LLMClient, LLMError
from motwi.ai.parse import ParseError, parse_verdicts
from motwi.ai.prompt import BatchItem, build_request
from motwi.ai.providers import PROVIDERS, client_for
from motwi.ai.scorer import Scorer
from motwi.app import MotwiApp, ai_key, effective_ai, seed_if_empty, stream_context
from motwi.engine.decide import decide_ai
from motwi.events import (
    AIStateChanged,
    EventBus,
    LoginFinished,
    RecapReady,
    StatusChanged,
    StreamChanged,
)
from motwi.live import LiveConfig
from motwi.models import Ceiling, ChatMessage, Kind, Rule, Source, Status, Unchecked
from motwi.moderation import AlreadyResolved, ExecutorError
from motwi.recap import build_recap, kind_totals, trigger_label
from motwi.settings import ProviderConfig, Settings
from motwi.store import ActionFilter, ActionRecord, Store
from motwi.twitch import BOT_SCOPES, SCOPES, client_id
from motwi.twitch.accounts import ensure_moderator, save_sign_in
from motwi.twitch.auth import (
    AuthError,
    AuthExpired,
    TwitchAuth,
    poll_device_flow,
    start_device_flow,
)
from motwi.twitch.convert import parse_ts
from motwi.twitch.eventsub import EventSubClient, Socket
from motwi.twitch.executor import HelixExecutor
from motwi.twitch.helix import HelixClient, HelixError
from motwi.twitch.tokens import Role, SecretStore, TokenStore, ai_key_name
from motwi.updates import UpdateError, fetch_latest, is_newer, update_url

log = logging.getLogger(__name__)

Connect = Callable[[str], Awaitable[Socket]]
TwitchState = Literal["signed_out", "idle", "connecting", "connected", "reconnect", "error"]
Slot = Literal["primary", "backup"]

RETRY_DELAYS_S = (5, 15, 30, 60)
EDITABLE_SETTINGS = {
    "strictness",
    "custom_thresholds",
    "ladder",
    "vip_leeway",
    "suggest_only",
    "hold_timeout_enabled",
    "hold_timeout_s",
    "warn_style",
    "delete_on_warn",
    "retention_days",
    "setup_complete",
    "check_updates",
}
OPEN_REQUEST = (Status.PENDING, Status.EXPIRED)
CHAT_RECENT = 100
UPDATE_FIRST_CHECK_S = 60
UPDATE_EVERY_S = 86_400


class NotConnected(Exception):
    """The operation needs a live Twitch connection."""


class FieldError(ValueError):
    """A setting or form value was rejected; `field` names it for the UI."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


async def _ws_connect(url: str) -> Socket:
    from websockets.asyncio.client import connect

    return await connect(url, open_timeout=10, max_size=2**20)


def subscriptions(broadcaster_id: str, moderator_id: str) -> list[tuple[str, str, dict]]:
    b = {"broadcaster_user_id": broadcaster_id}
    return [
        ("channel.chat.message", "1", {**b, "user_id": moderator_id}),
        ("channel.update", "2", b),
        ("stream.online", "1", b),
        ("stream.offline", "1", b),
    ]


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _midnight(days_ago: int, now: float) -> float:
    t = time.localtime(now - days_ago * 86400)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _month_start(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


@dataclass
class _Session:
    helix: HelixClient
    channel_helix: HelixClient
    executor: HelixExecutor
    app: MotwiApp
    broadcaster_id: str
    moderator_login: str


class Motwi:
    def __init__(
        self,
        *,
        store: Store,
        secrets: SecretStore,
        env: Mapping[str, str],
        connect: Connect = _ws_connect,
        clock: Callable[[], float] = time.time,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.bus = EventBus()
        self._secrets = secrets
        self._tokens = TokenStore(secrets)
        self._env = env
        self._connect_ws = connect
        self._clock = clock
        self._overrides = dict(overrides or {})
        self.live: LiveConfig
        self._keys: dict[ProviderConfig, str] = {}
        self._ai_problem: str | None = None
        self._http = httpx.AsyncClient(timeout=15)
        self._ai_http = httpx.AsyncClient()
        self._session: _Session | None = None
        self._session_task: asyncio.Task | None = None
        self._logins: dict[Role, asyncio.Task] = {}
        self._twitch: tuple[TwitchState, str] = ("idle", "")
        self._ai_state: tuple[str, str] = ("ok", "")
        self._persisted = Settings()
        self._update: dict[str, Any] = {
            "state": "idle",
            "latest": None,
            "url": None,
            "notes": "",
            "detail": "",
            "checked_at": None,
        }

    @classmethod
    async def open(cls, store: Store, **kwargs: Any) -> "Motwi":
        motwi = cls(store=store, **kwargs)
        await seed_if_empty(store)
        motwi.live = await LiveConfig.load(store)
        motwi._persisted = motwi.live.settings
        motwi._apply_overlay()
        return motwi

    async def aclose(self) -> None:
        for task in self._logins.values():
            task.cancel()
        await self.disconnect()
        await self._http.aclose()
        await self._ai_http.aclose()

    # live config

    def _apply_overlay(self) -> None:
        """Runtime view of the settings: overrides, and only the AI providers with keys."""
        settings = replace(self.live.settings, **self._overrides)
        self.live.settings, self._keys, self._ai_problem = effective_ai(
            settings, self._env, self._secrets
        )

    async def reload(self) -> None:
        await self.live.reload(self.store)
        self._persisted = self.live.settings
        self._apply_overlay()
        self.bus.publish(StatusChanged())

    def _client(self, cfg: ProviderConfig) -> LLMClient:
        key = self._keys.get(cfg)
        if key is None:
            raise LLMError(f"{cfg.provider}: no API key")
        return client_for(cfg, key, http=self._ai_http)

    # status

    @property
    def twitch_state(self) -> TwitchState:
        if self._tokens.load("streamer") is None:
            return "signed_out"
        return self._twitch[0]

    @property
    def twitch_detail(self) -> str:
        return self._twitch[1]

    @property
    def streamer_login(self) -> str | None:
        token = self._tokens.load("streamer")
        return token.login if token else None

    @property
    def moderator_login(self) -> str | None:
        return self._session.moderator_login if self._session else None

    def _set_twitch(self, state: TwitchState, detail: str = "") -> None:
        if (state, detail) != self._twitch:
            self._twitch = (state, detail)
            self.bus.publish(StatusChanged())

    def engine_state(self) -> str:
        twitch = self.twitch_state
        if twitch == "reconnect":
            return "reconnect"
        if twitch in ("signed_out", "idle", "error") or not self.live.settings.setup_complete:
            return "stopped"
        if twitch == "connecting":
            return "connecting"
        if self.live.settings.paused:
            return "paused"
        return "suggesting" if self.live.settings.suggest_only else "watching"

    def ai_state(self) -> tuple[str, str]:
        if self.live.settings.primary is None and self.live.settings.backup is None:
            return "off", self._ai_problem or "no AI provider configured"
        if self._session is None:
            return "ok", ""
        return self._ai_state

    def _account(self, role: Role) -> dict | None:
        token = self._tokens.load(role)
        return None if token is None else {"login": token.login, "user_id": token.user_id}

    def _provider_view(self, slot: Slot) -> dict | None:
        # the stored choice, even when it can't run for lack of a key; else the env's
        cfg = getattr(self._persisted, slot) or getattr(self.live.settings, slot)
        if cfg is None:
            return None
        return {
            "provider": cfg.provider,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "has_key": bool(ai_key(slot, cfg, self._env, self._secrets)),
            "key_from_env": bool(
                self._env.get(cfg.api_key_env if slot == "primary" else "MOTWI_AI_BACKUP_KEY")
            ),
        }

    async def status(self) -> dict:
        now = self._clock()
        stream = self.live.stream
        ai_state, ai_detail = self.ai_state()
        cost = (await self.store.usage_totals(since=_month_start(now))).cost_usd
        open_requests = await self.store.list_actions(
            where=ActionFilter(kinds=(Kind.BAN_REQUEST,), statuses=OPEN_REQUEST), limit=100
        )
        return {
            "version": __version__,
            "setup_complete": self.live.settings.setup_complete,
            "accounts": {"streamer": self._account("streamer"), "bot": self._account("bot")},
            "moderator": self.moderator_login,
            "twitch": {"state": self.twitch_state, "detail": self._twitch[1]},
            "stream": {
                "live": stream.stream_id is not None,
                "title": stream.title,
                "game": stream.game,
                "stream_id": stream.stream_id,
            },
            "engine": self.engine_state(),
            "suggest_only": self.live.settings.suggest_only,
            "paused": self.live.settings.paused,
            "ai": {
                "state": ai_state,
                "detail": ai_detail,
                "primary": self._provider_view("primary"),
                "backup": self._provider_view("backup"),
            },
            "month_cost": cost,
            "open_requests": len(open_requests),
            "update": {
                "available": self._update["state"] == "available",
                "version": self._update["latest"],
                "url": self._update["url"],
            },
        }

    # Twitch session

    async def connect(self) -> None:
        """Start the Twitch session unless it is running or can't run yet."""
        if self._session_task is not None and not self._session_task.done():
            return
        if self._tokens.load("streamer") is None:
            self._set_twitch("idle")
            return
        if not self.live.settings.setup_complete:
            return
        self._session_task = asyncio.create_task(self._run_session())

    async def disconnect(self) -> None:
        task, self._session_task = self._session_task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def reconnect(self) -> None:
        await self.disconnect()
        self._set_twitch("idle")
        await self.connect()

    async def wait_session(self) -> None:
        """Wait until the current session ends (for the headless `motwi run`)."""
        if self._session_task is not None:
            await asyncio.gather(self._session_task, return_exceptions=True)

    async def _run_session(self) -> None:
        attempt = 0
        while True:
            self._set_twitch("connecting", "")
            try:
                await self._session_once()
                return
            except asyncio.CancelledError:
                raise
            except AuthExpired as e:
                log.warning("Twitch sign-in expired: %s", e)
                self._set_twitch("reconnect", "Twitch sign-in expired. Reconnect Twitch.")
                return
            except ValueError as e:  # the bot is not a moderator, and the like
                self._set_twitch("error", str(e))
                return
            except (HelixError, httpx.HTTPError, OSError) as e:
                delay = RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)]
                attempt += 1
                log.warning("Twitch session failed (%s); retrying in %ss", e, delay)
                self._set_twitch("connecting", f"Can't reach Twitch, retrying in {delay}s")
                await asyncio.sleep(delay)
            except Exception:
                log.exception("Twitch session crashed")
                self._set_twitch("error", "Motwi hit an unexpected error. See the log.")
                return
            finally:
                self._session = None

    async def _session_once(self) -> None:
        cid = client_id()
        http = self._http
        streamer = TwitchAuth(http=http, client_id=cid, store=self._tokens, role="streamer")
        bot = (
            TwitchAuth(http=http, client_id=cid, store=self._tokens, role="bot")
            if self._tokens.load("bot") is not None
            else None
        )
        mod_auth = bot or streamer
        helix = HelixClient(mod_auth, http)
        channel_helix = HelixClient(streamer, http) if bot else helix
        broadcaster_id = streamer.token.user_id
        if bot is not None:
            await ensure_moderator(helix, bot.token, streamer.token)

        channel = await channel_helix.get_channel(broadcaster_id)
        live_stream = await channel_helix.get_stream(broadcaster_id)
        self.live.stream = stream_context(channel, live_stream)
        if live_stream and live_stream.get("id"):
            await self.store.stream_started(
                live_stream["id"],
                parse_ts(live_stream.get("started_at") or "", self._clock()),
                self.live.stream.title,
                self.live.stream.game,
            )
        await self._finish_unseen_streams(live_stream.get("id") if live_stream else None)
        self.bus.publish(StreamChanged(self.live.stream))

        expired = asyncio.Event()
        executor = HelixExecutor(helix, broadcaster_id=broadcaster_id, on_auth_expired=expired.set)
        app = MotwiApp(
            store=self.store,
            live=self.live,
            executor=executor,
            clients=self._client,
            self_user_ids={mod_auth.token.user_id},
            clock=self._clock,
            bus=self.bus,
            on_stream_end=self._finish_stream,
        )
        self._session = _Session(
            helix, channel_helix, executor, app, broadcaster_id, mod_auth.token.login
        )
        self._ai_state = ("ok", "")
        await self.store.ai_state_changed("ok", "", self._clock())  # a crash can leave one open
        ai_watch = asyncio.create_task(self._watch_ai())

        async def subscribe(session_id: str) -> None:
            for sub_type, version, condition in subscriptions(
                broadcaster_id, mod_auth.token.user_id
            ):
                await helix.subscribe(sub_type, version, condition, session_id)

        def on_state(state: str, detail: str) -> None:
            if state == "connected":
                self._set_twitch("connected", "")
            elif state == "revoked":
                self._set_twitch(
                    "reconnect", f"Twitch revoked access ({detail}). Reconnect Twitch."
                )
                expired.set()
            else:
                self._set_twitch("connecting", detail)

        eventsub = EventSubClient(
            connect=self._connect_ws, subscribe=subscribe, on_event=app.on_event, on_state=on_state
        )
        tasks = [
            asyncio.create_task(app.housekeeping()),
            asyncio.create_task(eventsub.run()),
            asyncio.create_task(expired.wait()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
            if expired.is_set():
                raise AuthExpired("Twitch sign-in expired")
        finally:
            for t in (*tasks, ai_watch):
                t.cancel()
            await asyncio.gather(*tasks, ai_watch, return_exceptions=True)
            await app.aclose()
            await self.store.ai_state_changed("ok", "", self._clock())

    async def _watch_ai(self) -> None:
        q = self.bus.subscribe()
        try:
            while True:
                e = await q.get()
                if isinstance(e, AIStateChanged):
                    self._ai_state = (e.state, e.detail)
                    await self.store.ai_state_changed(e.state, e.detail, self._clock())
                    self.bus.publish(StatusChanged())
        finally:
            self.bus.unsubscribe(q)

    # the end of a stream

    async def _finish_stream(
        self, stream_id: str, ended_at: float, *, seen_ending: bool = True
    ) -> None:
        """Save the recap and clear this-stream rules (spec 5.10). The rules go even if the
        recap fails, since they were only meant for that stream."""
        await self.store.stream_ended(stream_id, ended_at)
        recap = None
        try:
            if self._session is not None:
                await self._session.app.flush_counts()
            stream = await self.store.get_stream(stream_id)
            if stream is not None:
                recap = await build_recap(self.store, stream, seen_ending=seen_ending)
                await self.store.save_recap(stream_id, recap)
        except Exception:
            log.exception("could not build the recap for stream %s", stream_id)
        if await self.store.clear_stream_rules():
            await self.reload()
        if recap is not None and seen_ending:
            self.bus.publish(RecapReady(recap))

    async def _finish_unseen_streams(self, current_id: str | None) -> None:
        """Streams that ended while Motwi wasn't running. They end at their last known
        activity, since nothing better was recorded."""
        for stream in await self.store.open_streams():
            if stream.id != current_id:
                last = await self.store.last_activity(stream.id)
                await self._finish_stream(
                    stream.id, last if last is not None else stream.started_at, seen_ending=False
                )

    def _require_session(self) -> _Session:
        if self._session is None:
            raise NotConnected("Motwi isn't connected to Twitch right now")
        return self._session

    # sign-in

    async def begin_login(self, role: Role) -> dict:
        if role == "bot" and self._tokens.load("streamer") is None:
            raise FieldError("role", "Connect your own Twitch account first.")
        old = self._logins.pop(role, None)
        if old is not None:
            old.cancel()
        cid = client_id()
        scopes = BOT_SCOPES if role == "bot" else SCOPES
        code = await start_device_flow(self._http, cid, scopes)
        self._logins[role] = asyncio.create_task(self._finish_login(role, cid, scopes, code))
        return {
            "user_code": code.user_code,
            "verification_uri": code.verification_uri,
            "expires_in": code.expires_in,
        }

    def cancel_login(self, role: Role) -> None:
        task = self._logins.pop(role, None)
        if task is not None:
            task.cancel()

    async def _finish_login(self, role: Role, cid: str, scopes, code) -> None:
        try:
            token = await poll_device_flow(self._http, cid, scopes, code)
            previous = self._tokens.load("streamer")
            warning = await save_sign_in(
                role, token, tokens=self._tokens, http=self._http, client_id=cid
            )
            if role == "streamer" and previous and previous.user_id != token.user_id:
                # a different channel: the bot (if any) was checked against the old one
                log.info("streamer account changed from %s to %s", previous.login, token.login)
            self.bus.publish(LoginFinished(role, True, token.login, warning))
            await self.reconnect()
        except asyncio.CancelledError:
            raise
        except (AuthError, ValueError, HelixError, httpx.HTTPError) as e:
            self.bus.publish(LoginFinished(role, False, None, str(e)))
        except Exception:
            log.exception("sign-in failed")
            self.bus.publish(LoginFinished(role, False, None, "Sign-in failed. See the log."))
        finally:
            if self._logins.get(role) is asyncio.current_task():
                del self._logins[role]

    async def logout(self, role: Role) -> None:
        self._tokens.delete(role)
        if role == "streamer":
            self._tokens.delete("bot")  # a bot only makes sense for the streamer's channel
            await self.disconnect()
            self._set_twitch("idle")
        else:
            await self.reconnect()
        self.bus.publish(StatusChanged())

    # settings

    async def settings(self) -> dict:
        return (await self.store.get_settings()).to_dict()

    async def update_settings(self, changes: Mapping[str, Any]) -> dict:
        unknown = set(changes) - EDITABLE_SETTINGS
        if unknown:
            raise FieldError(min(unknown), "This setting can't be changed here.")
        current = (await self.store.get_settings()).to_dict()
        merged = {**current, **changes}
        try:
            new = Settings.from_dict(merged)
            new.validate()
        except (ValueError, TypeError, KeyError) as e:
            raise FieldError(next(iter(changes), ""), _friendly(str(e))) from e
        was_complete = self.live.settings.setup_complete
        await self.store.save_settings(new)
        await self.reload()
        if new.setup_complete and not was_complete:
            await self.connect()
        return new.to_dict()

    async def set_paused(self, paused: bool) -> None:
        stored = await self.store.get_settings()
        await self.store.save_settings(replace(stored, paused=paused))
        await self.reload()

    # rules, blocked words, people

    async def add_rule(self, text: str, ceiling: str, scope: str) -> Rule:
        text = text.strip()
        if not text:
            raise FieldError("text", "Write the rule first.")
        if len(text) > 300:
            raise FieldError("text", "Keep a rule under 300 characters.")
        if scope not in ("community", "stream"):
            raise FieldError("scope", "Unknown rule scope.")
        rule = await self.store.add_rule(text, _ceiling(ceiling), scope)  # type: ignore[arg-type]
        await self.reload()
        return rule

    async def update_rule(self, rule_id: str, changes: Mapping[str, Any]) -> Rule:
        rule = next((r for r in await self.store.list_rules() if r.id == rule_id), None)
        if rule is None:
            raise KeyError(rule_id)
        text = str(changes.get("text", rule.text)).strip()
        if not text:
            raise FieldError("text", "A rule can't be empty.")
        updated = Rule(rule.id, text, _ceiling(changes.get("ceiling", rule.ceiling)), rule.scope)
        await self.store.update_rule(updated)
        await self.reload()
        return updated

    async def delete_rule(self, rule_id: str) -> None:
        await self.store.delete_rule(rule_id)
        await self.reload()

    async def add_blocked(self, phrase: str, ceiling: str, hidden: bool) -> Any:
        phrase = " ".join(phrase.split())
        if not phrase:
            raise FieldError("phrase", "Type a word or phrase.")
        if len(phrase) > 100:
            raise FieldError("phrase", "Keep it under 100 characters.")
        word = await self.store.add_blocked(phrase, -1, _ceiling(ceiling), hidden)
        await self.reload()
        return word

    async def delete_blocked(self, word_id: str) -> None:
        await self.store.delete_blocked(word_id)
        await self.reload()

    async def people(self) -> dict:
        return {
            "trusted": await self.store.list_people("trusted"),
            "vip": await self.store.list_people("vip"),
            "vip_leeway": self.live.settings.vip_leeway,
            "recent": [
                {
                    "user_id": c.user_id,
                    "login": c.user_login,
                    "actions": c.actions,
                    "last_at": c.last_at,
                    "last_kind": c.last_kind.value,
                }
                for c in await self.store.recent_chatters(limit=20)
            ],
        }

    async def add_person(self, kind: str, login: str) -> None:
        login = login.strip().lstrip("@").lower()
        if kind not in ("trusted", "vip"):
            raise FieldError("kind", "Unknown list.")
        if not login or not all(c.isalnum() or c == "_" for c in login) or len(login) > 25:
            raise FieldError("login", "That doesn't look like a Twitch username.")
        await self.store.add_person(kind, login)  # type: ignore[arg-type]
        await self.reload()

    async def remove_person(self, kind: str, login: str) -> None:
        if kind not in ("trusted", "vip"):
            raise FieldError("kind", "Unknown list.")
        await self.store.remove_person(kind, login)  # type: ignore[arg-type]
        await self.reload()

    async def banned(self) -> list[dict]:
        s = self._require_session()
        rows = await s.channel_helix.banned_users(s.broadcaster_id)
        return [
            {
                "user_id": r.get("user_id"),
                "login": r.get("user_login"),
                "expires_at": r.get("expires_at") or None,
                "reason": r.get("reason") or "",
                "moderator": r.get("moderator_login"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ]

    async def unban(self, user_id: str) -> None:
        s = self._require_session()
        await s.executor.unban(user_id)

    # actions

    def actor(self) -> str:
        return self.streamer_login or "streamer"

    async def act(
        self, action_id: int, verb: str, actor: str | None = None, *, as_warn: bool = False
    ) -> ActionRecord:
        s = self._require_session()
        m = s.app.moderation
        who = actor or self.actor()
        if verb == "approve":
            return await m.approve(action_id, who, as_warn=as_warn)
        if verb == "deny":
            return await m.deny(action_id, who)
        if verb == "undo":
            return await m.undo(action_id, who)
        if verb == "retry":
            return await m.retry(action_id)
        raise KeyError(verb)

    async def actions(
        self, f: ActionFilter, *, before: int | None, limit: int
    ) -> list[ActionRecord]:
        return await self.store.list_actions(where=f, before_id=before, limit=limit)

    # updates

    def update_view(self) -> dict:
        return dict(self._update)

    async def check_update(self) -> dict:
        url = update_url(self._env)
        now = self._clock()
        if url is None:
            self._update = {**self._update, "state": "off", "detail": ""}
            return self.update_view()
        try:
            release = await fetch_latest(self._http, url, __version__)
        except UpdateError as e:
            # says nothing about whether an update exists; keep any offer already made
            self._update = {**self._update, "detail": str(e), "checked_at": now}
            if self._update["state"] != "available":
                self._update["state"] = "failed"
            return self.update_view()
        if release is None:
            state, latest, link, notes = "unpublished", None, None, ""
        elif is_newer(release, __version__):
            state, latest, link, notes = "available", release.version, release.url, release.notes
        else:
            state, latest, link, notes = "current", release.version, None, ""
        self._update = {
            "state": state,
            "latest": latest,
            "url": link,
            "notes": notes,
            "detail": "",
            "checked_at": now,
        }
        self.bus.publish(StatusChanged())
        return self.update_view()

    async def update_loop(
        self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        await sleep(UPDATE_FIRST_CHECK_S)
        while True:
            if self.live.settings.check_updates:
                try:
                    await self.check_update()
                except Exception:
                    log.exception("update check failed")
            await sleep(UPDATE_EVERY_S)

    # AI providers

    def ai_config(self) -> dict:
        return {
            "primary": self._provider_view("primary"),
            "backup": self._provider_view("backup"),
            "providers": {
                name: {"base_url": p.base_url, "default_model": p.default_model}
                for name, p in PROVIDERS.items()
            },
        }

    async def set_ai(
        self, slot: Slot, provider: str, model: str | None, base_url: str | None, key: str | None
    ) -> dict:
        cfg = _provider_config(provider, model, base_url)
        if key is not None:
            key = key.strip()
            if not key:
                raise FieldError("key", "Paste the key.")
            self._secrets.set(ai_key_name(slot), key)
        elif not ai_key(slot, cfg, self._env, self._secrets):
            raise FieldError("key", "Paste the key.")
        stored = await self.store.get_settings()
        await self.store.save_settings(replace(stored, **{slot: cfg}))
        await self.reload()
        await self._reset_ai()
        return self.ai_config()

    async def remove_backup(self) -> dict:
        stored = await self.store.get_settings()
        await self.store.save_settings(replace(stored, backup=None))
        self._secrets.delete(ai_key_name("backup"))
        await self.reload()
        await self._reset_ai()
        return self.ai_config()

    async def _reset_ai(self) -> None:
        if self._session is not None:
            await self._session.app.scorer.reset_clients()
            self._ai_state = ("ok", "")

    async def test_ai(
        self,
        slot: Slot,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        key: str | None = None,
    ) -> dict:
        """Score one harmless message with this provider. Works on unsaved values too."""
        stored = await self.store.get_settings()
        if provider is None:
            cfg = getattr(stored, slot)
            if cfg is None:
                raise FieldError("provider", "Pick a provider first.")
        else:
            cfg = _provider_config(provider, model, base_url)
        key = (key or "").strip() or ai_key(slot, cfg, self._env, self._secrets)
        if not key:
            raise FieldError("key", "Paste the key.")
        probe = ChatMessage(
            "test", "test", "viewer", "viewer", "gg that was close", "", frozenset(), 0
        )
        req = build_request([BatchItem(probe)], context=[], rules=[], stream=self.live.stream)
        client = client_for(cfg, key, http=self._ai_http)
        started = time.monotonic()
        try:
            completion = await client.complete(req.messages, timeout_s=15)
            parse_verdicts(completion.text, req)
        except LLMError as e:
            return {"ok": False, "detail": str(e)}
        except ParseError as e:
            return {
                "ok": False,
                "detail": f"The model replied, but not in the expected format ({e}).",
            }
        latency = round((time.monotonic() - started) * 1000)
        return {"ok": True, "latency_ms": latency, "model": completion.model}

    async def try_message(self, text: str) -> dict:
        text = text.strip()
        if not text:
            raise FieldError("text", "Type a message to try.")
        word = self.live.blocked.match(text)
        if word is not None:
            return {
                "source": Source.BLOCKED_WORD.value,
                "score": None,
                "rule": None,
                "reason": f"Blocked word: {'(hidden)' if word.hidden else word.phrase}",
                "action": {"kind": Kind.DELETE.value, "duration_s": None},
            }
        if self.live.settings.primary is None and self.live.settings.backup is None:
            raise FieldError("text", "Set up an AI provider in Settings first.")
        msg = ChatMessage(
            "try", "try", "viewer", "viewer", text[:500], text, frozenset(), self._clock()
        )
        scorer = self._session.app.scorer if self._session else None
        own = scorer is None
        if scorer is None:
            scorer = Scorer(
                live=self.live,
                clients=self._client,
                on_usage=self.store.record_usage,
                on_state=lambda *_: None,
            )
        try:
            result = await scorer.try_score(BatchItem(msg), [])
        finally:
            if own:
                await scorer.aclose()
        if isinstance(result, Unchecked):
            return {"error": f"The AI couldn't score it: {result.reason}"}
        plan = decide_ai(
            result,
            rules=self.live.rules_by_id(),
            thresholds=self.live.settings.thresholds(),
            ladder=self.live.settings.ladder,
            last_index=None,
        )
        rule = self.live.rules_by_id().get(result.rule_id) if result.rule_id else None
        return {
            "source": Source.AI.value,
            "score": result.score,
            "rule": None if rule is None else {"id": rule.id, "text": rule.text},
            "reason": result.reason,
            "action": {"kind": plan.kind.value, "duration_s": plan.duration_s},
        }

    # overview and stats

    async def overview(self) -> dict:
        now = self._clock()
        today = _midnight(0, now)
        counts = await self.store.action_counts(since=today)
        open_requests = await self.store.list_actions(
            where=ActionFilter(
                kinds=(Kind.BAN_REQUEST,), statuses=(*OPEN_REQUEST, Status.SUGGESTED)
            ),
            limit=50,
        )
        recent = await self.store.list_actions(limit=8)
        return {
            "today": {
                "checked": await self.store.count_checked(since_day=_day(now)),
                "actions": kind_totals(counts),
                "cost": (await self.store.usage_totals(since=today)).cost_usd,
                "unchecked": await self.store.count_unchecked(since=today),
            },
            "open_requests": open_requests,
            "recent": recent,
            "warnings": await self._warnings(now),
            "last_stream": _stream_view(next(iter(await self.store.list_streams(limit=1)), None)),
            "last_recap": await self.store.last_recap(),
        }

    async def chat_view(self) -> dict:
        """What the in-chat bar shows: this stream's, or without a stream today's, actions."""
        now = self._clock()
        stream_id = self.live.stream.stream_id
        since = None if stream_id else _midnight(0, now)
        counts = await self.store.action_counts(since=since, stream_id=stream_id)
        open_requests = await self.store.list_actions(
            where=ActionFilter(kinds=(Kind.BAN_REQUEST,), statuses=OPEN_REQUEST), limit=50
        )
        # no update nags in chat: it's for the app window, not for mid-stream
        warnings = [w for w in await self._warnings(now) if w["kind"] != "update"]
        expired = sum(a.status is Status.EXPIRED for a in open_requests)
        if expired:
            word = "request is" if expired == 1 else "requests are"
            warnings.append(
                {
                    "kind": "requests",
                    "level": "warn",
                    "text": f"{expired} ban {word} still waiting for an answer.",
                }
            )
        return {
            "status": await self.status(),
            "counts": _live_counts(counts),
            "recent": await self.store.list_actions(
                where=ActionFilter(stream_id=stream_id, since=since), limit=CHAT_RECENT
            ),
            "open_requests": open_requests,
            "warnings": warnings,
        }

    async def _warnings(self, now: float) -> list[dict]:
        out: list[dict] = []
        twitch = self.twitch_state
        if twitch == "reconnect" or twitch == "error":
            out.append({"kind": "twitch", "level": "error", "text": self._twitch[1]})
        elif twitch == "connecting" and self._twitch[1]:
            out.append({"kind": "twitch", "level": "warn", "text": self._twitch[1]})
        ai, detail = self.ai_state()
        if ai == "off":
            out.append({"kind": "ai", "level": "warn", "text": f"AI is off: {detail}"})
        elif ai == "down":
            out.append(
                {
                    "kind": "ai",
                    "level": "error",
                    "text": f"AI is down ({detail}). Blocked words and the spam filter still work.",
                }
            )
        elif ai == "degraded":
            out.append({"kind": "ai", "level": "warn", "text": f"Using the backup AI: {detail}"})
        overload = await self.store.count_unchecked(since=now - 600)
        if overload:
            out.append(
                {
                    "kind": "unchecked",
                    "level": "warn",
                    "text": f"{overload} messages in the last 10 minutes could not be checked.",
                }
            )
        if self._update["state"] == "available":
            out.append(
                {
                    "kind": "update",
                    "level": "info",
                    "text": f"Motwi {self._update['latest']} is available.",
                    "url": self._update["url"],
                }
            )
        return out

    async def stats(self, range_: str) -> dict:
        now = self._clock()
        stream_id: str | None = None
        since: float | None = None
        stream = None
        if range_ == "stream":
            stream = next(iter(await self.store.list_streams(limit=1)), None)
            if stream is None:
                return {"range": range_, "empty": True, "stream": None}
            stream_id = stream.id
        elif range_ in ("7d", "30d"):
            since = _midnight(int(range_[:-1]) - 1, now)
        else:
            raise FieldError("range", "Unknown range.")

        counts = await self.store.action_counts(since=since, stream_id=stream_id)
        per_day_rows = await self.store.actions_per_day(since=since, stream_id=stream_id)
        if since is not None:
            days = [_day(since + i * 86400 + 3600) for i in range(int(range_[:-1]))]
        else:
            days = sorted({d for d, _, _ in per_day_rows}) or [_day(stream.started_at)]
        per_day = {d: dict.fromkeys(("delete", "warn", "timeout", "ban_request"), 0) for d in days}
        for day, kind, n in per_day_rows:
            if day in per_day and kind.value in per_day[day]:
                per_day[day][kind.value] += n
        rules = {r.id: r for r in await self.store.list_rules()}
        triggers: dict[str, int] = {}
        for source, rule_id, n in await self.store.triggers(since=since, stream_id=stream_id):
            label = trigger_label(source, rule_id, rules)
            triggers[label] = triggers.get(label, 0) + n
        if since is not None:
            checked = await self.store.count_checked(since_day=_day(since))
            streams = len(await self.store.list_streams(since=since))
        else:
            checked = await self.store.count_checked(stream_id=stream_id)
            streams = 1
        usage = await self.store.usage_totals(since=since, stream_id=stream_id)
        return {
            "range": range_,
            "empty": not counts and not checked,
            "stream": _stream_view(stream),
            "checked": checked,
            "streams": streams,
            "totals": kind_totals(counts),
            "per_day": [{"day": d, **v} for d, v in per_day.items()],
            "triggers": sorted(
                ({"label": k, "count": v} for k, v in triggers.items()), key=lambda t: -t["count"]
            ),
            "cost": usage.cost_usd,
            "ai_requests": usage.requests,
        }


def _friendly(message: str) -> str:
    return message[:1].upper() + message[1:] + ("" if message.endswith(".") else ".")


def _ceiling(value: Any) -> Ceiling:
    try:
        return Ceiling(value)
    except ValueError as e:
        raise FieldError("ceiling", "Pick what this rule may do.") from e


def _provider_config(provider: str, model: str | None, base_url: str | None) -> ProviderConfig:
    if provider not in PROVIDERS:
        raise FieldError("provider", "Pick a provider.")
    model = (model or "").strip() or PROVIDERS[provider].default_model
    if not model:
        raise FieldError("model", "Type the model name.")
    base_url = (base_url or "").strip() or None
    if provider == "custom":
        if not base_url:
            raise FieldError("base_url", "Type the provider's base URL.")
        if not base_url.startswith(("https://", "http://")):
            raise FieldError("base_url", "The URL must start with https://")
    elif base_url is not None:
        base_url = None  # presets have fixed URLs
    return ProviderConfig(provider, model, base_url)  # type: ignore[arg-type]


def _live_counts(counts: Mapping[tuple[Kind, Status], int]) -> dict[str, int]:
    """Actions that stand right now, for the in-chat bar: no undone, denied or failed ones."""
    totals = kind_totals(counts)

    def standing(kind: str) -> int:
        t = totals[kind]
        return t["count"] - t["undone"] - t["denied"] - t["failed"]

    return {
        "delete": standing("delete"),
        "warn": standing("warn"),
        "timeout": standing("timeout"),
        "ban": standing("ban_request"),
        "suggested": sum(t["suggested"] for t in totals.values()),
    }


def _stream_view(s) -> dict | None:
    if s is None:
        return None
    return {
        "id": s.id,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "title": s.title,
        "game": s.game,
    }


__all__ = ["AlreadyResolved", "ExecutorError", "FieldError", "Motwi", "NotConnected"]
