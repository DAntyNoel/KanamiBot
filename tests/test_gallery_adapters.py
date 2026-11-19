from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_gallery_commands_across_adapters(tmp_path):
    script = dedent('''
        import asyncio
        import json
        import sys
        from io import BytesIO
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        import nonebot
        import httpx
        from nonebot.adapters.onebot.v11 import Adapter as OneAdapter, Bot as OneBot
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        from nonebot.adapters.qq import Bot
        from nonebot.adapters.qq.models import Dispatch, User
        from PIL import Image

        from qq_sandbox import create_app

        root = Path(sys.argv[1])
        credentials = root / "credentials.txt"
        credentials.write_text("appid:123456\\nsecret:test-secret")
        create_app(credentials)
        driver = nonebot.get_driver()
        adapter = driver._adapters["QQ"]
        bot = Bot(adapter, "123456", adapter.qq_config.qq_bots[0])
        bot.self_info = User(id="bot-user", username="Test", bot=True)

        from kanamibot.core import gallery_transport as transport, image_wrappers as store
        from kanamibot.core import media_storage
        from kanamibot.plugins import image as plugin

        store.DATA_ROOT = root / "media"
        media_storage.DATA_ROOT = store.DATA_ROOT
        media_storage.GLOBAL_INDEX_PATH = store.DATA_ROOT / "index.json"
        plugin.IMAGE_FOLDER = store.DATA_ROOT
        transport.CONFIG_PATH = root / "qqbot_gallery.json"
        transport.DATA_PATH = root / "group_manager.json"
        config = {"enable_writes": True, "group_bindings": {"group": 100},
                  "user_bindings": {"user": 200}, "superusers": ["user"]}
        transport.CONFIG_PATH.write_text(json.dumps(config))
        store.init_folder("图库", alias=["别名"], visibility=True)
        store.init_folder("私有", visibility=False)

        def picture(color):
            output = BytesIO()
            Image.new("RGB", (40, 40), color).save(output, format="PNG")
            return output.getvalue()

        pictures = [picture(color) for color in ("red", "green", "blue", "yellow")]
        quote = None
        count = 0
        sent = []
        uploads = []

        def event(command, attachments=None, group="group", role="owner", user="user"):
            global count
            count += 1
            data = {"id": str(count), "content": command,
                    "timestamp": "2026-11-19T12:00:00+08:00", "group_openid": group,
                    "author": {"id": user, "member_openid": user,
                               "bot": False, "member_role": role},
                    "attachments": attachments}
            result = adapter.payload_to_event(Dispatch(op=0, s=1, t="GROUP_MESSAGE_CREATE", d=data))
            if quote:
                result.reply = {"content": "", "msg_idx": quote}
                from nonebot.adapters.qq.models.qq import QQReplyMessage
                result.reply = QQReplyMessage.model_validate(result.reply)
            return result

        async def api(name, **kwargs):
            if name == "post_group_files":
                uploads.append(kwargs)
                return SimpleNamespace(file_info="uploaded")
            assert name == "post_group_messages", name
            idx = "ref-" + str(len(sent))
            sent.append(kwargs)
            return SimpleNamespace(id="send-" + idx, ext_info=SimpleNamespace(ref_idx=idx))

        async def download(event, bot):
            return [pictures[int(item.url.rsplit("/", 1)[1])] for item in (event.attachments or [])]

        async def command(text, **kwargs):
            previous = len(sent)
            await bot.handle_event(event(text, **kwargs))
            assert len(sent) > previous, text
            return sent[-1]

        async def check():
            global quote
            with patch.object(Bot, "call_api", side_effect=api), patch.object(
                transport, "download_images", side_effect=download
            ):
                for index, alias in enumerate(("保存", "存图", "收图", "save_image")):
                    result = await command(alias + " 别名 标签" + str(index), attachments=[
                        {"content_type": "image/png", "url": "https://example.com/" + str(index)}
                    ])
                    assert "保存了 1 张" in result["content"]
                    metadata = store.get_visible_images("图库", 100)[index]
                    assert metadata["tags"] == ["标签" + str(index)]
                assert len(store.get_visible_images("图库", 100)) == 4
                result = await command("保存 别名", attachments=[
                    {"content_type": "image/png", "url": "https://example.com/0"}
                ])
                assert "跳过 1 张" in result["content"]
                assert result["msg_type"] == 7
                assert len(store.get_visible_images("图库", 100)) == 4
                for alias in ("随机", "来点", "pick_image", "所有", "list_image"):
                    assert (await command("#" + alias + " 别名"))["msg_type"] == 7
                for alias in ("发送", "发", "选择", "select_image"):
                    for query in ("1", "-1", "标签0"):
                        assert (await command("/" + alias + " 别名 " + query))["msg_type"] == 7
                assert "索引超出" in (await command("发送 别名 0"))["content"]
                assert "索引超出" in (await command("发送 别名 5"))["content"]
                await command("发送 别名 1")
                quote = "ref-" + str(len(sent) - 1)
                for alias in ("信息", "info", "image_info"):
                    result = await command(alias)
                    assert "标签0" in result["content"]
                    assert "200 / 群 100" in result["content"]
                for alias in ("标签", "打标", "TAG", "tag"):
                    assert "标签已添加" in (await command(alias + " 新标签"))["content"]
                for alias in ("清除tag", "删除tag", "清空标签", "untag"):
                    assert "标签已全部清除" in (await command(alias))["content"]
                assert "已过期" in (await command("信息", group="other"))["content"]
                previous = len(sent)
                await bot.handle_event(event("删除", role="member", user="stranger"))
                assert len(sent) == previous
                assert "已删除" in (await command("删除"))["content"]
                assert len(store.get_visible_images("图库", 100)) == 3
                assert "已过期" in (await command("信息"))["content"]
                quote = None
                assert "请回复" in (await command("信息"))["content"]
                assert "该图库不存在" in (await command("保存 不存在", user="stranger"))["content"]
                assert "路径分隔符" in (await command("保存 ../outside"))["content"]
                for alias in ("移除", "remove_image", "delete_image"):
                    await command("发送 别名 1")
                    quote = "ref-" + str(len(sent) - 1)
                    assert "已删除" in (await command(alias))["content"]
                    quote = None
                await command("保存 新图库", attachments=[
                    {"content_type": "image/png", "url": "https://example.com/0"}
                ])
                assert store.get_folder_name("新图库") == "新图库"
                store.save_image(pictures[1], "png", "私有", group=100, qq=200, tags=["私有标签"])
                assert (await command("发送 私有 1"))["msg_type"] == 7
                assert "没有图片" in (await command("发送 私有 1", group="other"))["content"]
                assert "没有找到" in (await command("发送 私有 私有标签", group="other"))["content"]
                quote = None
                await command("发送 新图库 1")
                quote = "ref-" + str(len(sent) - 1)
                assert "保存了 1 张" in (await command("保存 回复图库"))["content"]
                quote = None
                blocked = {"100": {"modules": {"image": False}, "blacklist": []}}
                transport.DATA_PATH.write_text(json.dumps(blocked))
                previous = len(sent)
                await bot.handle_event(event("发送 新图库 1", user="stranger"))
                assert len(sent) == previous
                assert (await command("发送 新图库 1"))["msg_type"] == 7
                blocked["100"]["modules"]["image"] = True
                blocked["100"]["blacklist"] = [transport.user_id(event("", user="stranger"))]
                transport.DATA_PATH.write_text(json.dumps(blocked))
                previous = len(sent)
                await bot.handle_event(event("发送 新图库 1", user="stranger"))
                assert len(sent) == previous
                config["enable_writes"] = False
                transport.CONFIG_PATH.write_text(json.dumps(config))
                assert "尚未启用" in (await command("保存 别名"))["content"]
                assert (await command("发送 新图库 1"))["msg_type"] == 7

            from nonebot.adapters.qq.models.qq import QQReplyMessage
            quote = None
            download_event = event("保存 别名", attachments=[
                {"content_type": "image/png", "url": "https://example.com/0"}
            ])
            download_event.reply = QQReplyMessage(content="", attachments=[
                {"content_type": "image/png", "url": "https://example.com/0"},
                {"content_type": "image/png", "url": "https://example.com/1"},
            ])
            original_client = httpx.AsyncClient
            mock_transport = httpx.MockTransport(
                lambda request: httpx.Response(200, content=pictures[int(request.url.path[1:])])
            )
            with patch.object(transport.httpx, "AsyncClient", side_effect=lambda **kwargs:
                              original_client(transport=mock_transport, **kwargs)):
                assert await transport.download_images(download_event, bot) == pictures[:2]
            assert transport.group_id(event("", group="unbound")) < 0

            driver.register_adapter(OneAdapter)
            onebot = OneBot(driver._adapters["OneBot V11"], "999")
            from kanamibot.core.group_manager import group_config
            group_config.config["100"] = {"modules": {}, "blacklist": []}
            one_event = GroupMessageEvent(
                time=1, self_id=999, post_type="message", sub_type="normal",
                user_id=200, message_type="group", message_id=1, group_id=100,
                message="发送 新图库 1", original_message="发送 新图库 1",
                raw_message="发送 新图库 1", font=0,
                sender={"user_id": 200, "nickname": "Tester", "role": "owner"},
            )
            with patch.object(OneBot, "call_api", new_callable=AsyncMock) as one_api:
                one_api.return_value = {"message_id": 9876}
                await onebot.handle_event(one_event)
                one_api.assert_awaited_once()
                assert one_api.call_args.kwargs["message"][0].type == "image"
                assert plugin.send_buffer.get(9876) is not None
                assert plugin.send_buffer.get(9876, scope="qq:123456:group") is None

        asyncio.run(check())
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_storage_stale_instances_and_processes(tmp_path):
    script = dedent('''
        import json
        import subprocess
        from textwrap import dedent
        import sys
        from pathlib import Path
        import nonebot
        nonebot.init(_env_file=None)
        from kanamibot.core.media_storage import AdvancedMediaStorageSystem
        root = Path(sys.argv[1])
        first = AdvancedMediaStorageSystem("shared", data_root=root, refresh_global_index=False)
        stale = AdvancedMediaStorageSystem("shared", data_root=root, refresh_global_index=False)
        first.upload(b"first", ext=".bin", tags=["first"])
        stale.upload(b"second", ext=".bin", tags=["second"])
        assert len(first.list_files(return_type="dict")) == 2
        child = dedent("""
            import sys
            from pathlib import Path
            import nonebot
            nonebot.init(_env_file=None)
            from kanamibot.core.media_storage import AdvancedMediaStorageSystem
            store = AdvancedMediaStorageSystem(
                'shared', data_root=Path(sys.argv[1]), refresh_global_index=False
            )
            for index in range(8):
                store.upload((sys.argv[2] + str(index)).encode(), ext='.bin')
        """)
        processes = [subprocess.Popen([sys.executable, "-c", child, str(root), str(index)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                     for index in range(3)]
        for process in processes:
            _, error = process.communicate(timeout=60)
            assert process.returncode == 0, error
        assert len(stale.list_files(return_type="dict")) == 26
        assert stale.upload(b"first", ext=".bin")["status"] == "exists"
        first.update("0001", tags=["updated"])
        assert len(stale.list_files(return_type="dict")) == 26
        assert stale["0001"]["tags"] == ["updated"]
        stale.delete("0001")
        assert len(first.list_files(return_type="dict")) == 25
        for item in first.list_files(return_type="dict"):
            assert (first.files_dir / item["stored_filename"]).exists()
        stale.metadata_path.write_text("corrupt", encoding="utf-8")
        try:
            first.upload(b"must-not-write", ext=".bin")
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("corrupt registry must not be overwritten")
        assert stale.metadata_path.read_text() == "corrupt"
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
