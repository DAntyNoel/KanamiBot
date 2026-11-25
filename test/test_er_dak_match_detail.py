from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest
from erbs_plugin import ERBSConfig

nonebot.init()

from kanamibot.plugins.er_dak.match_detail import (  # noqa: E402
    MatchDetailRenderer,
    build_detail_card,
)


@pytest.mark.asyncio
async def test_match_detail_shows_shared_experiment_tasks_and_skips_legacy_teams(tmp_path):
    skia = pytest.importorskip("skia")
    client = SimpleNamespace(metadata=AsyncMock(return_value={}))
    started = {"experimentGroupId": 30011, "clearCount": 1}
    repeat = {"experimentGroupId": 30023, "clearCount": 3}
    team = {"gameId": 42, "teamNumber": 7, "gameRank": 1}
    rows = [
        {
            **team,
            "nickname": "队友",
            "experimentResultType": "Biotechnology",
            "questStepClear": [{**started, "firstClearElapsedSeconds": 260}, repeat],
            "crExperimentReward": 11,
            "crExperimentalCredit": 0,
        },
        {
            **team,
            "nickname": "玩家",
            "experimentResultType": "Biotechnology",
            "questStepClear": [{**started, "firstClearElapsedSeconds": 250.5}, repeat],
            "crExperimentReward": 11,
        },
        {"gameId": 42, "teamNumber": 4, "gameRank": 2, "nickname": "旧数据"},
    ]
    payload = await build_detail_card(client, {"matches": rows}, "玩家", 42)
    first, second = payload.sections
    assert first["experiment"]["firstClearSeconds"] == 250
    assert [task["count"] for task in first["experiment"]["repeatTasks"]] == [0, 0, 3]
    assert first["items"][0]["experimentalCredit"] == 0
    assert "experimentalCredit" not in first["items"][1]
    assert "experiment" not in second

    config = ERBSConfig(asset_directory=tmp_path, download_render_assets=False)
    async with MatchDetailRenderer(config) as renderer:
        png = skia.Image.MakeFromEncoded(await renderer.render(payload))
    assert png.width() == 1100
    assert png.height() == 560
