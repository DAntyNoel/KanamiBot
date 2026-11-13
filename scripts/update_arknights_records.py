"""Refresh the public operator/alter catalog and reviewed community nickname seed.

Usage: python scripts/update_arknights_records.py [--source-dir downloaded-json-directory]
Runtime nicknames under data/arknights are never changed by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/kanamibot/arknights/assets"
GAME = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/"
URLS = {
    "character_table.json": GAME + "character_table.json",
    "char_meta_table.json": GAME + "char_meta_table.json",
    "handbook_team_table.json": GAME + "handbook_team_table.json",
    "community_aliases.json": "http://106.52.139.57:8000/replace/getGlobalReplace",
}
# Reviewed spellings from AmiyaBot's community table. Restrict the seed to useful
# query names rather than copying every personal joke or mistaken replacement.
REVIEWED = set("""
小羊 银老板 前夫哥 鸡翅 海胆 自爆小车 近卫小车 医疗小车 42姐 桃子 桃金郎
冬将军 小火龙 小乐 王小姐 浊蒂 塞妈 姑妈 闪盾 白咕咕 ff0 德狗 拉狗 龟龟
kkdy 小马哥 鲨鲨 蒂蒂 莱茵拳皇 客师傅 小虎鲸 玛莉亚 索娜 苹果派 小脑斧
海猫 海蛞蝓 劳伦缇娜 机枪哥 夹子妹 kokodayo 唯一神 森rua 麦旋风 撕卡帝
洁哥 达不溜 闪剑圣 蕾缪乐 红蒂 流泪猫猫头 小海豚 水陈 卡子姐 老猞猁 二队长
歌蒂 ash 小僧 纳豆拌饭 异格陈 异格炎熔 香香 影子哥 莱瓦汀 大猫猫 早子姐
一拳超人 小燕子 小莫 白送 小企鹅 小刻 氪二百 雷子姐 火子姐 大尾巴狼 阿能
老爷子 塞爹 凯爹 王大锤 将军 大学生 猫猫头 欣特莱雅 莱娜 咸鱼 推王
我们的光 艾沃娜 佐菲娅 内鬼 鬼鬼 玛利亚 玛利亚临光 萨卡兹大妹子 贞德
臭鼬 叉烧猫 陈晖洁 艾利奥特 可颂 面包人 大小姐 恩雅希瓦艾什 艾尔斯
索尼娅 安东尼西蒙 克玛尔 席德佳 米娜 拉达 梅莉 丽兹 丽萨 苦艾
柳德米拉 祖玛玛 拉芙希妮 阿莱塔 简薇洛 埃内斯托萨拉斯 拉菲艾拉席尔瓦
路易莎 深靛 蒂娜 粉毛 苏茜 牛头人 菲尼克斯 苦难陈述者 异格克洛丝 异克
小鳄鱼 不死鸟 小凤凰 肥鸭 毁灭凤凰人 鸡精 雪鸡 大羊 卡姐 弦宝 卡密
耀光 欢乐水牛 异格鲨鲨 异格芙蓉 暴风眼 风暴眼 小老虎 近卫临光 芙蓉王
客门 异格格雷伊 小鸟 埃琳娜 炸弹人 德华 小天鹅 耀骑士 归鲨
异格嘉维尔 嘉百炼 百嘉 奶嘉 赛雷娅 安塞尔 幽冥鲨 叔叔 二爷 小黑
近卫嘉维尔 大哥大 牛牛 异格德克萨斯 异格德狗 异德 张飞 鳄鱼王 异格苇草
大哥 四爷 阿芙朵嘉 异格夜刀 异格黑角 充电宝 麒麟夜刀 火龙黑角 尤里卡
异格幽灵鲨 缪缪 蛇蛇 异格赫默 重装小车 焰苇 异格送葬人 异格阿葬 圣葬
鬼姐 猫车 纯爱 纯艾 异格艾雅法拉 异格小羊 异格诗怀雅 琳诗 奶羊 土豆
伊芙芙 异格杰西卡 dj ins 阿尔图罗 烛骑士 vvan 图图 黑骑士 三冠王
小锅盖 ela iana doc fuze 特蕾西娅 大铁老师 异格w ew 益达 logos
三队长 马鹿 玛露希尔 维娜 异格推王 异格推进之王 维娜维多利亚 乌拉
星棘 异格煌 香蕉姐 m3 克里斯汀小姐 克里斯汀 异惊 异格星熊 mmk 萌萌香
祥子 异格银灰 异格初雪 火陈
""".split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    tables, sources = {}, []
    for filename, url in URLS.items():
        if args.source_dir:
            raw = (args.source_dir / filename).read_bytes()
        else:
            request = Request(url, headers={"User-Agent": "KanamiBot Arknights records updater"})
            with urlopen(request, timeout=60) as response:
                raw = response.read()
        tables[filename] = json.loads(raw)
        sources.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest()})
    characters = tables["character_table.json"]
    teams = tables["handbook_team_table.json"]
    operators = []
    for char_id, row in characters.items():
        if row.get("isNotObtainable") or not char_id.startswith("char_"):
            continue
        faction = next((teams.get(row.get(key), {}).get("powerName")
                        for key in ("teamId", "groupId", "nationId")
                        if teams.get(row.get(key), {}).get("powerName")), "未分类")
        operators.append({"id": char_id, "name": row["name"], "faction": faction})
    names = {row["name"]: row for row in operators}
    groups = []
    for group in tables["char_meta_table.json"]["spCharGroups"].values():
        members = [characters[key]["name"] for key in group
                   if key in characters and characters[key]["name"] in names]
        if len(set(members)) > 1 and members not in groups:
            groups.append(list(dict.fromkeys(members)))
    aliases = {}
    for row in tables["community_aliases.json"]["data"]:
        name, alias = row["origin"], row["replace"]
        if name not in names or alias not in REVIEWED or not row.get("is_active"):
            continue
        if alias.casefold() in {value.casefold() for value in names}:
            continue
        key = alias.casefold()
        if key in aliases and aliases[key]["name"] != name:
            raise ValueError(f"ambiguous seed nickname: {alias}")
        aliases[key] = {"alias": alias, "name": name, "faction": names[name]["faction"],
                        "source": "AmiyaBot 社群全局绰号表"}
    # Cross-checked in the referenced community nickname article; no prose copied.
    aliases["42"] = {"alias": "42", "name": "史尔特尔", "faction": names["史尔特尔"]["faction"],
                     "source": "https://m.ali213.net/news/gl2408/1479047.html"}
    retrieved = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    ASSETS.mkdir(parents=True, exist_ok=True)
    for filename, payload in {
        "operators.json": {"retrieved": retrieved, "sources": sources[:3],
                           "operators": operators, "alter_groups": groups},
        "nicknames.json": {"retrieved": retrieved, "sources": sources[3:],
                           "aliases": sorted(
                               aliases.values(), key=lambda row: (row["faction"], row["name"])
                           )},
    }.items():
        (ASSETS / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8"
        )
    print(f"{len(operators)} operators, {len(groups)} alter groups, {len(aliases)} nicknames")


if __name__ == "__main__":
    main()
