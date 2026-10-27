from __future__ import annotations

import asyncio
import importlib
import sqlite3
import subprocess
import sys
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot
import pytest
from arclet.alconna import Arparma
from nonebot.adapters.onebot.v11 import (
    Adapter,
    Bot,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.dependencies import Dependent
from nonebot.exception import StopPropagation
from nonebot.matcher import Matcher, current_event, current_matcher, matchers
from nonebot.message import check_and_run_matcher
from nonebot.permission import Permission

MESSAGE_IDS = count(1)


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    root = tmp_path_factory.mktemp("skland")
    nonebot.init(
        _env_file=None,
        driver="~fastapi+~httpx",
        log_level="WARNING",
        command_start=["/"],
        localstore_data_dir=root / "data",
        localstore_cache_dir=root / "cache",
        localstore_config_dir=root / "config",
    )
    # Other test modules can initialize NoneBot during collection; init is idempotent.
    for name, value in {
        "localstore_data_dir": root / "data",
        "localstore_cache_dir": root / "cache",
        "localstore_config_dir": root / "config",
        "command_start": {"/"},
    }.items():
        setattr(nonebot.get_driver().config, name, value)
    nonebot.get_driver().register_adapter(Adapter)
    assert nonebot.load_plugin("kanamibot.plugins.skland") is not None

    from nonebot_plugin_orm.__main__ import main as orm_main
    from nonebot_plugin_skland import hook

    from kanamibot.core.group_manager import group_config
    from kanamibot.plugins import skland as adapter

    orm_main(args=["upgrade"], standalone_mode=False)
    orm_main(args=["check"], standalone_mode=False)
    with patch.object(hook, "update_data_resources", new=AsyncMock()):
        asyncio.run(hook.startup())

    bot = Bot(
        SimpleNamespace(
            get_name=lambda: "OneBot V11",
            config=nonebot.get_driver().config,
            _call_api=AsyncMock(),
        ),
        "10000",
    )
    return SimpleNamespace(
        root=root,
        adapter=adapter,
        matcher=adapter.upstream.skland,
        groups=group_config,
        bot=bot,
    )


@pytest.fixture
def policy(runtime, monkeypatch):
    monkeypatch.setattr(
        runtime.groups,
        "config",
        {"20000": {"modules": {"skland": True}, "blacklist": []}},
    )
    monkeypatch.setattr(runtime.groups, "save", lambda: None)
    monkeypatch.setattr(nonebot.get_driver().config, "superusers", {"90000"})
    return runtime.groups.config["20000"]


def event(text="/ef", *, user=30000, group=20000, private=False):
    fields = {
        "time": 0,
        "self_id": 10000,
        "post_type": "message",
        "sub_type": "friend" if private else "normal",
        "message_type": "private" if private else "group",
        "user_id": user,
        "message_id": next(MESSAGE_IDS),
        "message": Message(text),
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": user},
    }
    if private:
        return PrivateMessageEvent(**fields)
    return GroupMessageEvent(**fields, group_id=group)


def dispatch(runtime, monkeypatch, message, parsed=None, sent=None):
    calls = []

    async def handler(result: Arparma):
        calls.append("handler")
        if parsed is not None:
            parsed.append(result)

    monkeypatch.setattr(
        runtime.matcher,
        "handlers",
        [Dependent.parse(call=handler, allow_types=runtime.matcher.HANDLER_PARAM_TYPES)],
    )
    with (
        patch(
            "kanamibot.plugins.skland.commands.render_help_images",
            new=AsyncMock(return_value=(b"png1", b"png2", b"png3")),
        ),
        patch.object(runtime.bot, "send", new=AsyncMock()) as send,
    ):
        asyncio.run(check_and_run_matcher(runtime.matcher, runtime.bot, message, {}))
    if sent is not None:
        sent.extend(call.args[1] for call in send.await_args_list)
    return calls, send.await_count


def test_upstream_is_loaded_from_submodule_with_original_identity(runtime):
    plugin = nonebot.get_plugin_by_module_name("nonebot_plugin_skland")
    assert plugin is not None
    assert plugin.name == "nonebot_plugin_skland"
    checkout = Path(runtime.adapter.__file__).parent / "_upstream"
    assert Path(plugin.module.__file__).is_relative_to(checkout)


def test_migrations_create_account_and_history_tables_in_isolated_storage(runtime):
    database = runtime.root / "data/nonebot_plugin_orm/db.sqlite3"
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert {"skland_user", "skland_characters", "skland_gacha_record"} <= tables
        assert connection.execute("SELECT count(*) FROM alembic_version").fetchone()[0] > 0


@pytest.mark.parametrize("text", ["/ef", "/skland efcard", "/终末地抽卡记录"])
def test_enabled_commands_keep_upstream_parsing(runtime, policy, monkeypatch, text):
    assert dispatch(runtime, monkeypatch, event(text)) == (["handler"], 0)


@pytest.mark.parametrize("head", ["#SK", "#sk", "#森空岛"])
@pytest.mark.parametrize(
    ("body", "path", "value"),
    [
        ("扫码登录", "qrcode", None),
        ("登录", "qrcode", None),
        ("登陆", "qrcode", None),
        ("扫码", "qrcode", None),
        ("QR", "qrcode", None),
        ("qr", "qrcode", None),
        ("扫码绑定", "qrcode", None),
        ("森空岛角色", "char", None),
        ("切换终末地角色 2", "char.set.index", 2),
        ("角色更新", "char.update", None),
        ("ef -r 2 -a -s", "efcard.role.role_index", 2),
        ("终末地签到", "efsign.sign.all", None),
        ("终末地签到 -r 2", "efsign.sign.role.role_index", 2),
        ("终末地签到详情", "efsign.status", None),
        ("终末地抽卡记录 -l 3", "efgacha.limit.limit", 3),
        ("战争回响 -s -1 -w 2", "efwar.season.season_id", -1),
        ("森空岛解绑", "unbind", None),
        ("qrcode", "qrcode", None),
        ("efcard -r 2", "efcard.role.role_index", 2),
        ("方舟 -r 2", "role.role_index", 2),
    ],
)
def test_local_heads_preserve_command_and_options(
    runtime, policy, monkeypatch, head, body, path, value
):
    parsed = []
    assert dispatch(runtime, monkeypatch, event(f"{head} {body}"), parsed) == (["handler"], 0)
    assert parsed[0].find(path)
    if value is not None:
        assert parsed[0].query(path) == value


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ("森空岛绑定 example-token -u", "bind.update"),
        ("明日方舟签到 -r 2", "arksign.sign.role.role_index"),
        ("签到详情 -r 2", "arksign.status.role.role_index"),
        ("方舟抽卡记录 -b 0 -l 3", "gacha.limit.limit"),
        ("导入抽卡记录 https://example.invalid/history.json -r 2", "import.role.role_index"),
        ("傀影肉鸽", "rogue.topic.topic_name"),
        ("水月肉鸽", "rogue.topic.topic_name"),
        ("萨米肉鸽", "rogue.topic.topic_name"),
        ("萨卡兹肉鸽", "rogue.topic.topic_name"),
        ("界园肉鸽", "rogue.topic.topic_name"),
        ("树海肉鸽", "rogue.topic.topic_name"),
        ("战绩详情 1 -r 2 -f", "rginfo.favored"),
        ("收藏战绩详情 1 -r 2", "rginfo.favored"),
        ("方舟干员 -r 2 -ra 5,6 -n 阿米娅", "box.rarity.rarities"),
        ("全体签到", "arksign.all"),
        ("全体签到详情", "arksign.status.all"),
        ("终末地全体签到", "efsign.all"),
        ("终末地全体签到详情", "efsign.status.all"),
        ("全体角色更新", "char.update.all"),
        ("资源更新 --force", "sync.force"),
        ("sync --img --force --update", "sync.img"),
    ],
)
def test_full_menu_examples_reach_expected_upstream_action(
    runtime, policy, monkeypatch, body, path
):
    parsed = []
    assert dispatch(runtime, monkeypatch, event(f"#SK {body}"), parsed) == (["handler"], 0)
    assert parsed[0].find(path)


@pytest.mark.parametrize("head", ["#SK", "#sk", "#森空岛"])
@pytest.mark.parametrize("action", ["登录", "登陆", "扫码", "QR", "qr", "扫码登录", "扫码绑定"])
def test_login_does_not_depend_on_startup_shortcuts(runtime, policy, monkeypatch, head, action):
    command = runtime.matcher.command()
    command.shortcut("扫码绑定", delete=True)
    try:
        parsed = []
        assert dispatch(runtime, monkeypatch, event(f"{head} {action}"), parsed) == (["handler"], 0)
        assert parsed[0].find("qrcode")
    finally:
        command.shortcut("扫码绑定", {"command": "skland qrcode", "fuzzy": False, "prefix": True})


@pytest.mark.parametrize("head", ["#SK", "#sk", "#森空岛"])
@pytest.mark.parametrize("action", ["登录", "登陆", "扫码", "QR", "qr", "扫码登录", "扫码绑定"])
def test_login_runs_real_upstream_qrcode_handler(runtime, policy, head, action):
    with (
        patch("nonebot_plugin_skland.commands.bind.qrcode_handler", new=AsyncMock()) as login,
        patch.object(
            runtime.bot, "get_group_info", new=AsyncMock(return_value={"group_name": "test"})
        ),
        patch.object(
            runtime.bot, "get_group_member_info", new=AsyncMock(return_value={"join_time": 0})
        ),
        patch.object(runtime.bot, "send", new=AsyncMock()) as send,
    ):
        asyncio.run(
            check_and_run_matcher(runtime.matcher, runtime.bot, event(f"{head} {action}"), {})
        )
    login.assert_awaited_once()
    send.assert_not_awaited()


@pytest.mark.parametrize("head", ["#SK", "#sk", "#森空岛"])
@pytest.mark.parametrize("body", ["", " 帮助", " --help"])
def test_local_help(runtime, policy, monkeypatch, head, body):
    assert dispatch(runtime, monkeypatch, event(head + body)) == ([], 1)


@pytest.mark.parametrize(
    "text",
    [
        "#SK 帮助",
        "#sk",
        "#森空岛 --help",
        "/skland --help",
        "#SK efcard --help",
        "/skland --shortcut list",
    ],
)
def test_help_sends_images_without_raw_formatter_or_shortcut_mappings(
    runtime, policy, monkeypatch, text
):
    sent = []
    assert dispatch(runtime, monkeypatch, event(text), sent=sent) == ([], 1)
    assert len(sent) == 1
    assert [segment.type for segment in sent[0]] == ["image", "image", "image"]


def test_help_render_failure_does_not_fall_back_to_raw_text(runtime):
    from kanamibot.plugins.skland.commands import SklandCommandExtension

    with patch(
        "kanamibot.plugins.skland.commands.render_help_images",
        new=AsyncMock(side_effect=RuntimeError("browser unavailable")),
    ):
        message = asyncio.run(
            SklandCommandExtension().output_converter("help", "RAW => skland qrcode")
        )
    assert str(message) == "帮助图片暂时无法生成，请稍后重试。"


def test_parse_error_does_not_echo_internal_commands_or_credentials(runtime):
    from kanamibot.plugins.skland.commands import SklandCommandExtension

    message = asyncio.run(
        SklandCommandExtension().output_converter("error", "secret-token => skland bind")
    )
    assert str(message) == "指令参数有误，请发送 #SK 帮助 查看命令和用法。"


def test_help_covers_all_native_function_paths_and_contains_no_mapping_table(runtime):
    from arclet.alconna import Subcommand

    from kanamibot.plugins.skland.help import HELP_PAGES, help_documents

    def paths(options, parent=""):
        result = set()
        for option in options:
            if not isinstance(option, Subcommand):
                continue
            path = parent + option.dest
            result.add(path)
            result.update(paths(option.options, path + "."))
        return result

    documented = {
        path
        for page in HELP_PAGES
        for section in page.sections
        for entry in section.entries
        for path in entry.paths
    }
    expected = paths(runtime.matcher.command().options)
    # Container subcommands are covered by their documented actions.
    assert all(
        path in documented or any(item.startswith(path + ".") for item in documented)
        for path in expected
    )
    documents = help_documents()
    assert len(documents) == 3
    assert all("#SK" in html and "#sk" in html and "#森空岛" in html for html in documents)
    assert all(
        token not in "".join(documents)
        for token in ("=&gt;", "=&gt", "At|int", "skland qrcode", "快捷命令:", "代理", "Alconna")
    )
    assert "&lt;token或cred&gt;" in documents[0]


@pytest.mark.parametrize("space", ["  ", "\t", "\u3000"])
def test_local_head_accepts_whitespace_separator(runtime, policy, monkeypatch, space):
    assert dispatch(runtime, monkeypatch, event(f"#SK{space}扫码登录")) == (["handler"], 0)


@pytest.mark.parametrize("text", ["#SKY 扫码登录", "#SK扫码登录", "聊天 #SK 扫码登录"])
def test_command_head_requires_boundary_and_message_start(runtime, policy, monkeypatch, text):
    assert dispatch(runtime, monkeypatch, event(text)) == ([], 0)


def test_local_command_keeps_mention_and_original_message(runtime, policy, monkeypatch):
    from nonebot.adapters.onebot.v11 import MessageSegment
    from nonebot_plugin_alconna import At

    message = event("#SK ef ")
    message.message += MessageSegment.at(30001)
    original = message.message.copy()
    parsed = []
    assert dispatch(runtime, monkeypatch, message, parsed) == (["handler"], 0)
    target = parsed[0].query("efcard.target")
    assert isinstance(target, At) and target.target == "30001"
    assert message.message == original


@pytest.mark.parametrize(
    "text", ["/ef", "/skland --help", "/skland bad-option", "#SK 扫码登录", "#sk 帮助"]
)
def test_disabled_group_sends_neither_results_nor_automatic_help(
    runtime,
    policy,
    monkeypatch,
    text,
):
    policy["modules"]["skland"] = False
    assert dispatch(runtime, monkeypatch, event(text)) == ([], 0)


def test_enabled_help_still_responds(runtime, policy, monkeypatch):
    assert dispatch(runtime, monkeypatch, event("/skland --help")) == ([], 1)


def test_blacklisted_member_is_blocked(runtime, policy, monkeypatch):
    policy["blacklist"].append(30000)
    assert dispatch(runtime, monkeypatch, event()) == ([], 0)


@pytest.mark.parametrize("private", [False, True])
def test_superuser_and_private_chat_follow_existing_core_policy(
    runtime,
    policy,
    monkeypatch,
    private,
):
    policy["modules"]["skland"] = False
    message = event(private=private, user=30000 if private else 90000)
    assert dispatch(runtime, monkeypatch, message) == (["handler"], 0)


def test_original_permission_is_preserved(runtime, policy):
    from kanamibot.plugins.skland.permissions import protect_matcher

    async def original_permission():
        return False

    matcher = Matcher.new("message", permission=Permission(original_permission))
    try:
        protect_matcher(matcher)
        assert not asyncio.run(matcher.check_perm(runtime.bot, event()))
    finally:
        matcher.destroy()


def test_waiter_rechecks_group_policy_and_keeps_sender_session(runtime, policy):
    from nonebot_plugin_waiter import waiter

    async def run():
        origin = event("/skland qrcode")
        matcher_token = current_matcher.set(runtime.matcher())
        event_token = current_event.set(origin)

        async def accept(event: GroupMessageEvent):
            return event.get_plaintext()

        try:
            wait = waiter(["message"], keep_session=True)(accept)
        finally:
            current_event.reset(event_token)
            current_matcher.reset(matcher_token)

        before = set(matchers[0])
        task = asyncio.create_task(wait.wait(timeout=2))
        await asyncio.sleep(0)
        listener = (set(matchers[0]) - before).pop()
        try:
            policy["modules"]["skland"] = False
            await check_and_run_matcher(listener, runtime.bot, event("确认"), {})
            assert not task.done()
            policy["modules"]["skland"] = True
            for message in (event("确认", user=30001), event("确认", group=20001)):
                await check_and_run_matcher(listener, runtime.bot, message, {})
                assert not task.done()
            try:
                await check_and_run_matcher(listener, runtime.bot, event("确认"), {})
            except StopPropagation:
                pass
            assert await task == "确认"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_help_contains_one_public_skland_entry(runtime):
    from kanamibot.plugins.help import collect_features

    features = [feature for feature in collect_features() if "skland" in feature.module_name]
    assert len(features) == 1
    assert features[0].title == "森空岛"
    assert "#SK 帮助" in features[0].usage
    assert "#SK、#sk、#森空岛" in features[0].usage


def test_storage_defaults_preserve_explicit_paths(runtime, monkeypatch, tmp_path):
    settings = importlib.import_module("kanamibot.plugins.skland.config")
    config = SimpleNamespace(localstore_data_dir=tmp_path)
    monkeypatch.setattr(settings, "get_driver", lambda: SimpleNamespace(config=config))
    settings.configure_storage()
    assert config.localstore_data_dir == tmp_path
    assert config.localstore_cache_dir == settings.DATA_DIR / "cache"
    assert config.localstore_config_dir == settings.DATA_DIR / "plugin_configs"


def test_browser_uses_playwright_install_directory(runtime, monkeypatch, tmp_path):
    settings = importlib.import_module("kanamibot.plugins.skland.config")
    config = SimpleNamespace()
    monkeypatch.setattr(settings, "get_driver", lambda: SimpleNamespace(config=config))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    settings.configure_browser()
    assert config.render["provider_config"]["storage_path"] == tmp_path


def test_browser_preserves_explicit_render_configuration(runtime, monkeypatch):
    settings = importlib.import_module("kanamibot.plugins.skland.config")
    config = SimpleNamespace(render={"provider_config": {"channel": "msedge"}})
    original = config.render
    monkeypatch.setattr(settings, "get_driver", lambda: SimpleNamespace(config=config))
    settings.configure_browser()
    assert config.render is original


@pytest.mark.parametrize("enabled", [False, True])
def test_global_switches_in_fresh_process(tmp_path, enabled):
    code = f"""
from pathlib import Path
import nonebot
root = Path({str(tmp_path)!r})
nonebot.init(
    _env_file=None, driver='~fastapi+~httpx', log_level='WARNING',
    localstore_data_dir=root/'data', localstore_cache_dir=root/'cache',
    localstore_config_dir=root/'config', kanamibot_skland_enabled={enabled!r},
    kanamibot_skland_auto_sign=False,
)
assert nonebot.load_plugin('kanamibot.plugins.skland') is not None
assert (nonebot.get_plugin_by_module_name('nonebot_plugin_skland') is not None) == {enabled!r}
if {enabled!r}:
    from nonebot_plugin_apscheduler import scheduler
    assert scheduler.get_job('daily_arksign') is None
    assert scheduler.get_job('daily_efsign') is None
    assert scheduler.get_job('daily_resource_update') is not None
print('SWITCH_OK')
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SWITCH_OK" in result.stdout
