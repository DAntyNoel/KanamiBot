"""Public command reference and image menus for Skland."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


@dataclass(frozen=True)
class Entry:
    command: str
    usage: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    title: str
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class HelpPage:
    title: str
    subtitle: str
    sections: tuple[Section, ...]
    notes: tuple[str, ...] = ()


HELP_PAGES = (
    HelpPage(
        "终末地与账号",
        "先扫码绑定，再查看角色、签到与抽卡记录。",
        (
            Section(
                "终末地",
                (
                    Entry(
                        "#SK ef [目标] [-r 序号] [-a] [-s]",
                        "角色面板；-a 展示全部干员，-s 使用简化背景。",
                        ("efcard",),
                    ),
                    Entry(
                        "#SK 终末地签到 [-r 序号]",
                        "签到本人全部角色；带 -r 时只签到指定角色。",
                        ("efsign.sign",),
                    ),
                    Entry(
                        "#SK 终末地签到详情 [-r 序号]",
                        "查看本人的签到结果和失败原因。",
                        ("efsign.status",),
                    ),
                    Entry(
                        "#SK 终末地抽卡记录 [目标] [-r 序号] [-b 起点] [-l 终点]",
                        "同步并查询角色池、武器池；-b / -l 为各类卡池起止索引，"
                        "从 0 计数，终点不含。",
                        ("efgacha",),
                    ),
                    Entry(
                        "#SK 战争回响 [目标] [-r 序号] [-s 赛季] [-w 轮换]",
                        "默认查询当前赛季与轮换；-s -1 查询上一赛季。",
                        ("efwar",),
                    ),
                ),
            ),
            Section(
                "账号与角色",
                (
                    Entry(
                        "#SK 扫码登录",
                        "使用森空岛 App 扫码，核对角色后回复「确认」保存；"
                        "也支持「登录」「扫码绑定」。",
                        ("qrcode",),
                    ),
                    Entry(
                        "#SK 森空岛绑定 <token或cred> [-u]",
                        "仅私聊；新增账号，或用 -u 更新凭证对应的已有账号。",
                        ("bind",),
                    ),
                    Entry(
                        "#SK 森空岛角色", "查看全部账号、角色序号及两款游戏的默认角色。", ("char",)
                    ),
                    Entry("#SK 切换终末地角色 <序号>", "设置本人默认终末地角色。", ("char.set",)),
                    Entry("#SK 切换方舟角色 <序号>", "设置本人默认明日方舟角色。", ("char.set",)),
                    Entry("#SK 角色更新", "同步本人全部绑定账号的角色列表。", ("char.update",)),
                    Entry(
                        "#SK 森空岛解绑",
                        "交互选择一个或全部账号；确认后删除相应角色与抽卡记录。",
                        ("unbind",),
                    ),
                    Entry("#SK 帮助", "查看全部命令图片；也支持只发送 #SK，或 #SK --help。"),
                ),
            ),
        ),
        (
            "[参数] 可省略，<参数> 必填；输入时去掉括号。指令头与功能之间留空格。",
            "目标可用 @用户 或 QQ 号；-r 仅选择自己的角色，不能同时查询他人。",
            "示例：#SK ef -r 2 -a -s　　#SK 战争回响 -s -1 -w 2",
        ),
    ),
    HelpPage(
        "明日方舟",
        "角色状态、集成战略、寻访记录与干员筛选。",
        (
            Section(
                "查询与签到",
                (
                    Entry(
                        "#SK 方舟 [目标] [-r 序号]", "查看理智、基建、招募与默认角色状态。", ("",)
                    ),
                    Entry(
                        "#SK 明日方舟签到 [-r 序号]",
                        "签到本人全部方舟角色；带 -r 时只签到指定角色。",
                        ("arksign.sign",),
                    ),
                    Entry(
                        "#SK 签到详情 [-r 序号]",
                        "查看本人的方舟签到结果和失败原因。",
                        ("arksign.status",),
                    ),
                    Entry(
                        "#SK 方舟抽卡记录 [目标] [-r 序号] [-b 起点] [-l 终点]",
                        "默认展示前 3 个卡池；-b / -l 为起止索引，从 0 计数，终点不含。",
                        ("gacha",),
                    ),
                    Entry(
                        "#SK 导入抽卡记录 <链接> [-r 序号]",
                        "导入小黑盒导出的寻访记录；记录 UID 必须与所选角色一致。",
                        ("import",),
                    ),
                ),
            ),
            Section(
                "集成战略",
                (
                    Entry(
                        "#SK 水月肉鸽 [目标] [-r 序号]",
                        "查看对应主题的生涯与历史战绩。",
                        ("rogue",),
                    ),
                    Entry(
                        "#SK <主题>肉鸽 [目标] [-r 序号]",
                        "主题：傀影、水月、萨米、萨卡兹、界园、树海；树海对应黑流树海。",
                    ),
                    Entry(
                        "#SK 战绩详情 <ID> [-r 序号] [-f]",
                        "回复战绩图片查看单局；-f 查看收藏；带 -r 时获取指定角色的新数据。",
                        ("rginfo",),
                    ),
                    Entry(
                        "#SK 收藏战绩详情 <ID> [-r 序号]",
                        "查看收藏的单局战绩，ID 使用战绩图片中的编号。",
                        ("rginfo",),
                    ),
                ),
            ),
            Section(
                "干员筛选",
                (
                    Entry(
                        "#SK 方舟干员 [目标] [筛选词] [-r 序号]",
                        "筛选词用空格分隔，例如：6星 近卫 满潜；也支持 未拥有、5-6星、练度。",
                        ("box",),
                    ),
                    Entry(
                        "-o 持有状态　-ra 星级　-p 职业　-b 分支",
                        "持有状态：owned / unowned / all；星级：6、5,6、4-6 或 all。",
                    ),
                    Entry(
                        "--position 位置　--gender 性别　-f 势力　--race 种族",
                        "位置、性别、职业分支、势力与种族可填游戏中的名称。",
                    ),
                    Entry(
                        "--potential 潜能　-s 排序　-n 名称",
                        "潜能：6、5,6、3-6；排序：release 实装 / acquired 获取 / training 练度。",
                    ),
                ),
            ),
        ),
        (
            "示例：#SK 方舟干员 6星 近卫 满潜　　#SK 方舟干员 -r 2 -ra 5,6 -n 阿米娅",
            "同一维度取「或」，不同维度取「且」；查询他人时，目标放在筛选词之前。",
            "未拥有模式不支持潜能、获取或练度筛选；-r 是选角，-ra 是星级。",
        ),
    ),
    HelpPage(
        "管理与图片交互",
        "管理功能仅限超级用户；群开关沿用本群管理权限。",
        (
            Section(
                "超级用户命令",
                (
                    Entry("#SK 全体签到", "为机器人全部用户的明日方舟角色签到。", ("arksign.all",)),
                    Entry(
                        "#SK 全体签到详情", "查看全部用户的明日方舟签到结果。", ("arksign.status",)
                    ),
                    Entry(
                        "#SK 终末地全体签到", "为机器人全部用户的终末地角色签到。", ("efsign.all",)
                    ),
                    Entry(
                        "#SK 终末地全体签到详情",
                        "查看全部用户的终末地签到结果。",
                        ("efsign.status",),
                    ),
                    Entry("#SK 全体角色更新", "逐账号同步全部用户的角色列表。", ("char.update",)),
                    Entry(
                        "#SK 资源更新 [--force]",
                        "更新游戏与卡池数据；--force 忽略版本检查。",
                        ("sync",),
                    ),
                    Entry(
                        "#SK sync [--data] [--img] [--force] [--update]",
                        "--data 更新数据，--img 更新图片，--update 覆盖旧图片；"
                        "不选类型时两者都更新。",
                        ("sync",),
                    ),
                ),
            ),
            Section(
                "本群功能开关",
                (
                    Entry(
                        "/enable skland　　/disable skland",
                        "启用或停用本群查询；不影响账号后台自动签到。",
                    ),
                    Entry(
                        "/list module", "查看本群模块状态；这些管理命令以机器人全局前缀配置为准。"
                    ),
                ),
            ),
            Section(
                "回复图片后发送",
                (
                    Entry("background", "回复带背景的角色卡，获取背景原图；无需 #SK 前缀。"),
                    Entry("clue", "回复支持线索功能的方舟角色卡，查看线索板；无需 #SK 前缀。"),
                ),
            ),
        ),
        (
            "全体签到面向机器人全部用户；个人的「终末地签到」「明日方舟签到」仅操作本人。",
            "个人签到不带参数时覆盖本人全部角色；带 -r 序号时只操作所选角色。",
            "凭证只在私聊提交；更新终末地抽卡记录需要扫码或 token 绑定的账号。",
        ),
    ),
)

HELP_SUMMARY = "#SK 帮助：查看全部命令图片；指令头支持 #SK、#sk、#森空岛。"
_images: tuple[bytes, ...] | None = None
_render_lock = asyncio.Lock()


@lru_cache(maxsize=1)
def help_documents() -> tuple[str, ...]:
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    template = environment.get_template("help.html")
    return tuple(
        template.render(page=page, index=index, total=len(HELP_PAGES))
        for index, page in enumerate(HELP_PAGES, start=1)
    )


async def render_help_images() -> tuple[bytes, ...]:
    global _images
    if _images is not None:
        return _images
    async with _render_lock:
        if _images is None:
            from nonebot_plugin_skland.compact import html_to_pic

            images = []
            for html in help_documents():
                images.append(
                    await html_to_pic(
                        html, device_scale_factor=1.5, viewport={"width": 1000, "height": 10}
                    )
                )
            _images = tuple(images)
    return _images
