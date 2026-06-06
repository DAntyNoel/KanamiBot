"""
:Author:  NekoRabi
:Update Time:  2022/8/28 3:14
:Describe: 雀魂相关功能的实现
:Version: 0.6.5
"""
# ruff: noqa: E501

import logging
import math
import random
import re
import sqlite3
import time
from typing import Any

import httpx

from kanamibot.core.paths import DATA_DIR
from kanamibot.core.utils import messagechain_builder, text_to_imagebytes

SHARE_FILES_DIR = DATA_DIR
DB_PATH = SHARE_FILES_DIR / "majsoul" / "majsoul.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


class ClientTimeout:
    def __init__(self, total: float | None = None) -> None:
        self.total = total


class TCPConnector:
    def __init__(self, *, ssl: bool = True, limit: int | None = None) -> None:
        self.ssl = ssl
        self.limit = limit


class _ResponseContext:
    def __init__(self, request: Any) -> None:
        self._request = request
        self._response: httpx.Response | None = None

    async def __aenter__(self) -> "_ResponseContext":
        try:
            self._response = await self._request
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._response is not None:
            await self._response.aclose()

    @property
    def status(self) -> int:
        return self._response.status_code if self._response is not None else 0

    async def json(self) -> Any:
        if self._response is None:
            return None
        return self._response.json()


class ClientSession:
    def __init__(
        self,
        *,
        connector: TCPConnector | None = None,
        timeout: ClientTimeout | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        limits = httpx.Limits(max_connections=connector.limit if connector else None)
        self._client = httpx.AsyncClient(
            headers=headers,
            limits=limits,
            timeout=timeout.total if timeout else None,
            verify=connector.ssl if connector else True,
        )

    async def __aenter__(self) -> "ClientSession":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self._client.aclose()

    def get(self, url: str) -> _ResponseContext:
        return _ResponseContext(self._client.get(url))


fontsize = 36

levellist = [[1200, 1400, 2000], [2800, 3200, 3600], [4000, 6000, 9000]]

_link_index = 1

match_level = {
    4: {
        "金东": 8,
        "金": '8.9',
        "金南": '9',
        "玉东": 11,
        "玉": '11.12',
        '玉南': '12',
        "王座东": 15,
        "王座南": 16,
        '王座': '15.16',
        '王': '15.16',
        'all': '8.9.11.12.15.16'
    },
    3: {
        "金东": 21,
        "金南": '22',
        "金": '21.22',
        "玉东": 23,
        "玉": '23.24',
        '玉南': 24,
        "王座东": 25,
        "王座南": 26,
        '王座': '25.26',
        '王': '25.26',
        'all': '21.22.23.24.25.26'
    }
}

_match_level_name = ['all', '金', '金东', '金南', '玉', '玉东', '玉南', '王', '王座', '王座东', '王座南']

infomodel = dict(基本=['和牌率', '放铳率', '自摸率', '默听率', '流局率', '流听率', '副露率', '立直率', '和了巡数', '平均打点', '平均铳点', '平均顺位', '被飞率'],
                 立直=['立直率', '立直和了', '立直放铳A', '立直放铳B', '立直收支', '立直收入', '立直支出', '先制率', '追立率', '被追率', '立直巡目', '立直流局',
                     '一发率', '振听率', '立直多面', '立直好型'],
                 更多=['最大连庄', '里宝率', '被炸率', '平均被炸点数', '放铳时立直率', '放铳时副露率', '副露后放铳率', '副露后流局率', '副露后和牌率', '打点效率', '铳点损失',
                     '净打点效率'],
                 血统=['役满', '累计役满', '两立直', '流满', '最大累计番数', '平均起手向听'])

user_agent_list = [
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/67.0.3396.99 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) Gecko/20100101 Firefox/61.0",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/64.0.3282.186 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/62.0.3202.62 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/45.0.2454.101 Safari/537.36",
    "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0)",
    "Mozilla/5.0 (Macintosh; U; PPC Mac OS X 10.5; en-US; rv:1.9.2.15) Gecko/20110303 Firefox/3.6.15",
]

aiotimeout = ClientTimeout(total=25)

serverErrorHTML = '<html><body><h1>503 Service Unavailable</h1>'
serverErrorCode = 503  # 牌谱屋炸了

# _template = read_file(r"./config/Majsoul/template.yml")
_config = dict(qhpt=True, qhinfo=True, qhsl=True, qhyb=True, qhpaipu=True, broadcast='image', query_limit=10, linknum=1,
               link_update=True, link_freshtime='2:33', autoquery=True, dailydrawcount=3, disptgroup=[0],
               authenticationgroup=[0], disinfogroup=[0], disslgroup=[0], disybgroup=[0], disautoquerygroup=[0],
               dispaipugroup=[0])
_query_limit = _config.get('query_limit', 10)

if _query_limit < 1:
    print('同时最大请求数量已自动调整为10')
    _query_limit = 10
if not isinstance(_query_limit, int):
    _query_limit = 10
    print('同时最大请求数量已自动调整为10')


def get_qhpturl(playername, searchtype=3):
    """查询玩家的URL"""
    if searchtype in [3, '3']:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl3/search_player/{playername}?limit=20&tag=all"
    else:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl4/search_player/{playername}?limit=20&tag=all"
    return url


def get_player_records_url(playerid, searchtype, end_time=None, start_time=1262304000000, total=599):
    """
    获取玩家对局记录的URL

    Args:
        playerid: 玩家牌谱屋id
        searchtype: 查询类型
        end_time: 结束时间
        start_time: 开始时间
        total: 最多数量

    Returns:

    """
    if end_time is None:
        end_time = int(time.time() * 1000)
    if searchtype in [4, '4']:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl4/player_records/{playerid}/{end_time}/{start_time}?limit={total}&mode=8,9,11,12,15,16&descending=true"
    else:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl3/player_records/{playerid}/{end_time}/{start_time}?limit={total}&mode=21,22,23,24,25,26&descending=true"
    return url


def get_paipuurl(playerid, searchtype, count):
    """
    获取玩家最近牌谱的URL

    Args:
        playerid: 玩家牌谱屋id
        searchtype: 查询类型
        count: 查询数量

    Returns:

    """
    nowtime = time.time()
    nowtime = math.floor(nowtime / 10) * 10000 + 9999
    if int(searchtype) == 4:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl4/player_records/{playerid}/{nowtime}/1262304000000?limit={count}&mode=8,9,11,12,15,16&descending=true"
    else:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl3/player_records/{playerid}/{nowtime}/1262304000000?limit={count}&mode=21,22,23,24,25,26&descending=true"
    return url


def get_player_extended_stats_url(playerid, searchtype, end_time=None, start_time=None, mode=None):
    """
    获取玩家数据的URL

    Args:
        playerid: 玩家牌谱屋id
        searchtype: 查询类型
        end_time: 结束时间
        start_time: 开始时间
        mode: 查询场况的的字符串

    Returns: URL

    """
    if not (start_time or end_time):
        nowtime = time.time()
        nowtime = math.floor(nowtime / 10) * 10000 + 9999
        start_time = 1262304000000
        end_time = nowtime
    if mode:
        mode = match_level.get(int(searchtype), 3).get(mode)
    else:
        if int(searchtype) == 4:
            mode = '8.9.11.12.15.16'
        else:
            mode = '21.22.23.24.25.26'
    url = f'https://{_link_index}.data.amae-koromo.com/api/v2/pl{searchtype}/player_extended_stats/{playerid}/{start_time}/{end_time}?mode={mode}'
    return url


def get_pturl_by_pid(playerid, seatchtype):
    nowtime = time.time()
    nowtime = math.floor(nowtime / 10) * 10000 + 9999
    if seatchtype in [3, '3']:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl{seatchtype}/player_stats/{playerid}/1262304000000/{nowtime}?mode=26.24.22.25.23.21"
    else:
        url = f"https://{_link_index}.data.amae-koromo.com/api/v2/pl{seatchtype}/player_stats/{playerid}/1262304000000/{nowtime}?mode=8,9,11,12,15,16"
    return url


class MajsoulQuery:

    # def __init__(self):
    #     self.template = _template

    @staticmethod
    async def getplayerdetail(playername: str, selecttype: str = None, selectlevel: list = None,
                              model='基本') -> list:
        """
        获取玩家详情

        Args:
            playername:     玩家名
            selecttype:     三麻 or 四麻 (3/4)
            selectlevel:    查询场况 金玉王
            model:          查询类型

        Returns:    包含结果的Mirai消息链

        """
        if model is None:
            model = '基本'
        if selecttype is None:
            selecttype = 4
        if selectlevel is None:
            selectlevel = 'all'
        if model not in ['基本', '更多', '立直', '血统', 'all']:
            return await messagechain_builder(text="无法识别类型参数，可用的参数为'基本'、'更多'、'立直'、'血统'、'all'")
        if selectlevel not in _match_level_name:
            return await messagechain_builder(text='场况参数缺失或有误,请输入正确的段位场,如"玉"、"金东"')
        playerid = get_playerid(playername)
        if not playerid:
            return await messagechain_builder(text="查询失败,数据库中无此用户,请先用 qhpt 查询该用户。")
        rule = "三麻"

        try:
            url = get_player_extended_stats_url(playerid, selecttype, mode=selectlevel)
            if f'{selecttype}' == "4":
                rule = "四麻"
            async with ClientSession(
                    connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                    headers={'User-Agent': random.choice(user_agent_list)}) as session:
                async with session.get(url) as response:
                    if response.status == 503:
                        if not _config.get('silence_CLI', False):
                            print('牌谱屋似乎离线了')
                        return await messagechain_builder(text="牌谱屋似乎离线了~")
                    content = await response.json()
        except TimeoutError as e:
            if not _config.get('silence_CLI', False):
                print(f"查询超时:\t{e}\n")
            return await messagechain_builder(text="查询超时,请稍后再试")

        except httpx.ConnectError as _e:
            if not _config.get('silence_CLI', False):
                print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
            return await messagechain_builder(text="查询超时,请稍后再试")
        if content.get('error', False):
            return await messagechain_builder(text='未找到该玩家在这个场次的的对局')
        msg = f" 以下是玩家 {playername} 的{rule}{selectlevel if selectlevel else ''}数据:\n"
        for (k, v) in content.items():
            if not isinstance(v, (list, dict)):
                if str(k) in ["id", "count"]:
                    continue
                if model in ['基本', '更多', '血统', '立直']:
                    if str(k) in infomodel.get(model):
                        if isinstance(v, float):
                            if str(k).endswith('率'):
                                msg += f"{k:<12} : {v * 100:2.2f}%\n"
                            else:
                                msg += f"{k:<12} : {v:2.2f}\n"
                        else:
                            msg += f"{k:<12} : {v if v else 0}\n"
                elif model == 'all':
                    if isinstance(v, float):
                        if str(k).endswith('率'):
                            msg += f"{k:<12} : {v * 100:2.2f}%\n"
                        else:
                            msg += f"{k:<12} : {v:2.2f}\n"
                    else:
                        msg += f"{k:<12} : {v if v else 0}\n"
        _broadcast_type = _config.get('broadcast', 'image').lower()
        if _broadcast_type in ['txt', 'text', 'str']:
            return await messagechain_builder(text=msg)
        else:
            return await messagechain_builder(imgbase64=text_to_imagebytes(fontsize=36, text=msg, needtobase64=True))

    @staticmethod
    async def getsomeqhpaipu(playername: str, seatchtype="4", counts=5) -> list:
        """
        获取玩家牌谱

        Args:
            playername: 玩家名
            seatchtype: 查询类型, 3 or 4
            counts: 查询数量

        Returns: 包含结果的Mirai消息链

        """
        if counts is None:
            counts = '5'
        try:
            counts = int(counts)
        except (TypeError, ValueError):
            return await messagechain_builder(text="牌局数量有误，请输入 1-10 的整数")
        if seatchtype is None:
            seatchtype = '4'
        if counts < 1 or counts > 10:
            return await messagechain_builder(text="牌局数量有误，最多支持10场牌局")
        if seatchtype not in ['3', '4', 3, 4]:
            return await messagechain_builder(text="牌局参数有误，请输入 3 或 4")
        ptupdate = 0
        ERROR = False
        playerid = get_playerid(playername)
        if not playerid:
            return await messagechain_builder(text="查询失败,数据库中无此用户,请先用 qhpt 查询该用户。")
        paipuInfo = f"最近{counts}场对局信息如下："
        _paipu_link = ''
        try:
            content = await get_player_records_byid(playerid, seatchtype, counts)
            for item in content:
                paipuuid = f'{item["uuid"]}'
                startTime = time.strftime(
                    '%Y-%m-%d %H:%M:%S', time.localtime(item["startTime"]))
                endTime = time.strftime('%Y-%m-%d %H:%M:%S',
                                        time.localtime(item["endTime"]))
                players = item['players']
                _broadcast_type = _config.get('broadcast', 'image').lower()
                if _broadcast_type in ['txt', 'text', 'str']:
                    paipuInfo += f"\n牌谱连接: https://game.maj-soul.net/1/?paipu={paipuuid}\n"
                    _paipu_link += f"https://game.maj-soul.net/1/?paipu={paipuuid}\n"
                else:
                    paipuInfo += f"\n牌谱UID: {paipuuid}\n"
                paipuInfo += f"开始时间: {startTime}\n结束时间: {endTime}\n对局玩家:\n"
                for player in players:
                    if player['nickname'].strip() == playername.strip():
                        ptupdate += int(player['gradingScore'])
                    paipuInfo += f"{player['nickname']} : {player['score']} ({player['gradingScore']})\n"
                paipuInfo += "\n"
            paipuInfo += f"\n总PT变化 : {ptupdate}"
        except TimeoutError as e:

            if not _config.get('silence_CLI', False):
                print(e)
            ERROR = True
            paipuInfo = '牌谱查询超时,请稍后再试'
        result = await messagechain_builder(text=paipuInfo)
        if not ERROR:
            _broadcast_type = _config.get('broadcast', 'image').lower()
            if _broadcast_type in ['txt', 'text', 'str']:
                return await messagechain_builder(text=paipuInfo)
            elif _broadcast_type in ['mix', 'mixed']:
                return await messagechain_builder(text=_paipu_link,
                                                  imgbase64=text_to_imagebytes(fontsize=36, text=paipuInfo,
                                                                          needtobase64=True))
            else:
                # text_to_image(fontsize=36, path=f"MajsoulInfo/qhpt{username}.png", text=prtmsg)
                return await messagechain_builder(
                    imgbase64=text_to_imagebytes(fontsize=36, text=paipuInfo, needtobase64=True))
            # result['img64'] = text_to_image(fontsize=36, text=paipuInfo, needtobase64=True)
        return result

    @staticmethod
    async def getmonthreport(playername: str, selecttype: str = None, year: str = None,
                             month: str = None):
        """
        获取玩家月报

        Args:
            playername: 玩家名
            selecttype: 3 or 4
            year: 年
            month: 月

        Returns: 包含结果的Mirai消息链

        """

        ptchange = 0
        msg = ""
        getrecent = False
        if not selecttype:
            selecttype = "4"
        if not year or not month:
            year, month = time.strftime("%Y-%m", time.localtime()).split('-')
            paipumsg = f"{playername} 最近一个月 的对局报告\n"
            getrecent = True
        else:
            if 1 > int(month) or int(month) > 12:
                return await messagechain_builder(text="请输入正确的时间")
            paipumsg = f"{playername} {year}-{month} 的对局报告\n"
        selectmonth = f"{year}-{month}"
        rankdict = {"1": 0, "2": 0, "3": 0, "4": 0, "fly": 0}
        playerslist = []
        if month == "12":
            nextmonth = f"{int(year) + 1}-1"
        else:
            nextmonth = f"{year}-{int(month) + 1}"
        playerid = get_playerid(playername)
        if not playerid:
            return await messagechain_builder(text="查询失败,数据库中无此用户,请先用 qhpt 查询该用户。")
        selectmontht = int(time.mktime(time.strptime(selectmonth, '%Y-%m')) * 1000)
        if getrecent:
            nextmontht = int(time.time() * 1000)
            selectmontht = nextmontht - 2592000 * 1000
        else:
            nextmontht = int(time.mktime(time.strptime(nextmonth, '%Y-%m')) * 1000)

        try:
            url = get_player_records_url(playerid, selecttype, nextmontht, selectmontht)
            async with ClientSession(
                    connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                    headers={'User-Agent': random.choice(user_agent_list)}) as session:
                async with session.get(url) as response:
                    if response.status == 503:
                        return await messagechain_builder(text='牌谱屋似乎离线了')
                    paipuresponse = await response.json()
                url = get_player_extended_stats_url(playerid, selecttype, end_time=nextmontht, start_time=selectmontht)
                async with session.get(url) as response:
                    if response.status == 503:
                        return await messagechain_builder(text='牌谱屋似乎离线了')
                    inforesponse: dict = await response.json()
                if len(paipuresponse) == 0:
                    return await messagechain_builder(text='该玩家这个月似乎没有进行过该类型的对局呢')
                paipumsg += f"总对局数: {len(paipuresponse)}\n其中"
                for players in paipuresponse:
                    temp = players['players']
                    temp.sort(key=getrank)
                    playerslist.append(temp)
                for playerrank in playerslist:
                    if selecttype == "4":
                        rank = 4
                    else:
                        rank = 3
                    for player in playerrank:
                        if player['nickname'] == playername:
                            ptchange += player['gradingScore']
                            rankdict[f"{rank}"] += 1
                            if player['score'] < 0:
                                rankdict['fly'] += 1
                            break
                        rank = rank - 1
                averagerank = (rankdict['1'] + rankdict['2'] * 2 +
                               rankdict['3'] * 3 + rankdict['4'] * 4) / len(paipuresponse)
                if rankdict['1'] + rankdict['2'] + rankdict['3'] + rankdict['4'] < len(paipuresponse):
                    paipumsg += "玩家名疑似输入有误,分析顺位失败,请检查大小写\n"
                else:
                    if selecttype == "4":
                        paipumsg += f"{rankdict['1']}次①位,{rankdict['2']}次②位,{rankdict['3']}次③位,{rankdict['4']}次④位"
                    else:
                        paipumsg += f"{rankdict['1']}次①位,{rankdict['2']}次②位,{rankdict['3']}次③位"
                    if rankdict['fly'] > 0:
                        paipumsg += f",被飞了{rankdict['fly']}次"
                    paipumsg += f",平均顺位:{averagerank:1.2f}\nPT总得失: {ptchange}\n\n"
                msg += paipumsg
                infomsg = f" 立直率: {inforesponse.get('立直率', None) * 100 if inforesponse.get('立直率', None) else 0:2.2f}%\t"
                infomsg += f" 副露率: {inforesponse.get('副露率', None) * 100 if inforesponse.get('副露率', None) else 0:2.2f}%\t"
                infomsg += f" 和牌率: {inforesponse.get('和牌率', None) * 100 if inforesponse.get('和牌率', None) else 0:2.2f}%\n"
                infomsg += f" 放铳率: {inforesponse.get('放铳率', None) * 100 if inforesponse.get('放铳率', None) else 0:2.2f}% "
                if inforesponse.get('默听率', None):
                    infomsg += f"\t 默听率: {inforesponse.get('默听率', 0) * 100 :2.2f}%\n"
                else:
                    infomsg += '\t'
                infomsg += f" 平均打点: {inforesponse.get('平均打点') if inforesponse.get('平均打点') else 0}\t 平均铳点 : {inforesponse.get('平均铳点') if inforesponse.get('平均铳点') else 0}"
            msg += infomsg
        except TimeoutError as _e:
            print(f'获取雀魂详情 请求超时:\t{_e}')
            return await messagechain_builder(text="查询超时,请稍后再试")
        except httpx.ConnectError as _e:
            print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
            return await messagechain_builder(text="查询超时,请稍后再试")
        _broadcast_type = _config.get('broadcast', 'image').lower()
        if _broadcast_type in ['txt', 'text', 'str']:
            return await messagechain_builder(text=msg)
        return await messagechain_builder(imgbase64=text_to_imagebytes(fontsize=36, text=msg, needtobase64=True))

    @staticmethod
    async def asygetqhpaipu():
        """
        对局异步查询

        Returns: 分析整理后的新对局

        """
        nowtime = time.time()
        nowtime = math.floor(nowtime / 10) * 10000 + 9999
        cx = sqlite3.connect(DB_PATH)
        cursor = cx.cursor()
        cursor.execute("select playerid from watchedplayersview where watchedgroupcount > 0")
        playerids = cursor.fetchall()
        cursor.close()
        cx.close()
        playeridlist = []
        for item in playerids:
            playeridlist.append(item[0])
        results = await getmatchresult(playeridlist, nowtime)
        return msganalysis(results)

    @staticmethod
    async def query(username: str, selecttype: str = "", selectindex: int = 1) -> list:
        """查pt"""
        userinfo = await asyqhpt(username)
        if userinfo['error']:
            if userinfo['offline']:
                return await messagechain_builder(text='牌谱屋似乎离线了')
                # return dict(msg="牌谱屋服务器离线", error=True)
            return await messagechain_builder(text='查询超时,请稍后再试')
            # return dict(msg="查询超时,请稍后再试", error=True)
        playerid = userinfo.get('playerid', None)
        playername = userinfo.get('playername', None)
        prtmsg = f"{username}\n"
        if not playerid:
            return await messagechain_builder(text='该玩家不存在或未进行金之间及以上对局')
            # return dict(msg="该用户不存在", error=True)
        cx = sqlite3.connect(DB_PATH)
        cursor = cx.cursor()
        cx.commit()
        cursor.execute("select playerid from qhplayer where playername = ?", (playername,))
        if len(cursor.fetchall()) == 0:
            cursor.execute(
                "insert into qhplayer(playerid,playername) values(?,?)", (playerid, playername))
            cx.commit()
        cursor.close()
        cx.close()
        """三麻"""
        if userinfo.get('muti3', None):
            if not _config.get('silence_CLI', False):
                print("查到多位同名玩家，将输出第一个，请确认是否是匹配的用户,精确匹配请增加参数")
            prtmsg += "\n\n查到多位同名玩家，将输出第一个\n请确认是否是匹配的用户,精确匹配请增加参数\n"
        user_p3_levelinfo: dict = userinfo.get('pl3', None)
        if user_p3_levelinfo:
            user_p3_levelinfo = user_p3_levelinfo.get("level")
            p3_level = user_p3_levelinfo.get("id")
            p3_score = int(user_p3_levelinfo.get("score")) + int(user_p3_levelinfo.get("delta"))
            prtmsg += "\n" + levelswitch(p3_level, p3_score, "三麻")
        else:
            if not _config.get('silence_CLI', False):
                print("查询不到三麻段位")
            prtmsg += "\n未查询到三麻段位。"
        """四麻"""
        if userinfo.get('muti4', None):
            if not _config.get('silence_CLI', False):
                print("查到多位同名玩家，将输出第一个，请确认是否是匹配的用户,精确匹配请增加参数")
            prtmsg += "\n\n查到多位同名玩家，将输出第一个\n请确认是否是匹配的用户,精确匹配请增加参数\n"
        user_p4_levelinfo = userinfo.get('pl4', None)
        if user_p4_levelinfo:
            user_p4_levelinfo = user_p4_levelinfo.get("level")
            p4_level = user_p4_levelinfo.get("id")
            p4_score = int(user_p4_levelinfo.get("score")) + int(user_p4_levelinfo.get("delta"))
            prtmsg += "\n" + levelswitch(p4_level, p4_score, "四麻")
        else:
            if not _config.get('silence_CLI', False):
                print("查询不到四麻段位")
            prtmsg += "\n未查询到四麻段位。"
        _broadcast_type = _config.get('broadcast', 'image').lower()
        if _broadcast_type in ['txt', 'text', 'str']:
            return await messagechain_builder(text=prtmsg)
        else:
            # text_to_image(fontsize=36, path=f"MajsoulInfo/qhpt{playername}.png", text=prtmsg)
            img_bytes = text_to_imagebytes(fontsize=36, text=prtmsg)
            result = await messagechain_builder(imgbase64=img_bytes)
            return result
        # return dict(msg=prtmsg, error=False)

    @staticmethod
    async def getcertaininfo(username: str, selecttype: str = "4", selectindex: int = 1) -> list:
        """
        精确查pt

        Args:
            username:   玩家名
            selecttype:  查询类别  3/4 代表三麻或者四麻
            selectindex:  查询序号,于 0.6.4 改成 下标从1开始

        Returns:

        """
        if selectindex is None:
            selectindex = 1
        selectindex = int(selectindex) - 1 if int(selectindex) > 0 else 0
        if str(selecttype) == '3':
            url = get_qhpturl(username, 3)
            typename = "三麻"
        else:
            url = get_qhpturl(username, 4)
            typename = "四麻"
        try:
            async with ClientSession(
                    connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                    headers={'User-Agent': random.choice(user_agent_list)}) as session:
                async with session.get(url) as response:
                    if response.status == 503:
                        return await messagechain_builder(text="牌谱屋似乎离线了")
                    playerinfo = await response.json()
        except TimeoutError as e:
            if not _config.get('silence_CLI', False):
                print(f"查询超时\t {e}")
            return await messagechain_builder(text="查询超时,请稍后再试")

        except httpx.ConnectError as _e:
            if not _config.get('silence_CLI', False):
                print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
            return await messagechain_builder(text="查询超时,请稍后再试")
        if len(playerinfo) == 0:
            return await messagechain_builder(text="该玩家不存在或未进行金之间及以上对局")
        elif len(playerinfo) <= selectindex:
            return await messagechain_builder(text=f"序号有误，共查询到{len(playerinfo)}名玩家,序号最大值为{len(playerinfo)}")
        elif selectindex < 0:
            return await messagechain_builder(text="序号有误，序号一定大于0")
        else:
            playerinfo = playerinfo[selectindex]
        if playerinfo:
            playerid = playerinfo['id']
            playername = playerinfo['nickname']
            prtmsg = f"{playername}\n"
            levelinfo = playerinfo.get("level")
            level = levelinfo.get("id")
            if level > 20000 and str(selecttype) == '4':
                prtmsg = f"未查询到四麻玩家,查询到三麻玩家\n玩家名: {playername}"
            elif level < 20000 and str(selecttype) == '3':
                prtmsg = f"未查询到三麻玩家,查询到四麻玩家\n玩家名: {playername}"
            score = int(levelinfo.get("score")) + int(levelinfo.get("delta"))
            prtmsg += levelswitch(level, score, typename)
            cx = sqlite3.connect(DB_PATH)
            cursor = cx.cursor()
            cursor.execute("select id from qhplayer where playername = ?", (playername,))
            qhplayer = cursor.fetchone()
            if qhplayer is None:
                cursor.execute(
                    "insert into qhplayer(playerid,playername) values(?,?)", (playerid, playername))
            else:
                cursor.execute("update qhplayer set playerid = ? where id = ?", (playerid, qhplayer[0]))
            cx.commit()
            cx.close()
            _broadcast_type = _config.get('broadcast', 'image').lower()
            if _broadcast_type in ['txt', 'text', 'str']:
                return await messagechain_builder(text=prtmsg)
            else:
                # text_to_image(fontsize=36, path=f"MajsoulInfo/qhpt{username}.png", text=prtmsg)
                return await messagechain_builder(imgbase64=text_to_imagebytes(fontsize=36, text=prtmsg, needtobase64=True))
        return await messagechain_builder(text="查询失败")

    @staticmethod
    async def set_link_node() -> int:
        """
        自动获取低延时链路并使用

        Returns:低延时链路编号  1~5

        """
        global _link_index
        config_link = _config.get('link_num', None)
        if config_link not in [1, 2, 3, 4, 5]:
            print('未设置牌谱屋默认链路,将进行链路检测')
            print('如需跳过,请设置 link_num 为 1-5')
        else:
            return config_link
        link_time = {1: 30, 2: 30, 3: 30, 4: 30, 5: 30}
        async with ClientSession(
                connector=TCPConnector(
                    ssl=False, limit=_config.get('query_limit', 10)),
                timeout=ClientTimeout(total=30),
                headers={'User-Agent': random.choice(user_agent_list)}) as session:
            for i in range(1, 6):
                try:
                    url = f'https://{i}.data.amae-koromo.com/api/v2/pl3/search_player/天才麻将杏杏?limit=20&tag=all'
                    start_time = time.time()
                    async with session.get(url) as response:
                        if response.status == 503:
                            pass  # 先不做异常处理
                            link_time[i] = 30
                            print(f'牌谱屋似乎离线了,测试默认节点{i}失败')
                        elif response.status != 200:
                            print(f'节点{i}链接失败')
                            link_time[i] = 30
                        else:
                            end_time = time.time()
                            link_time[i] = end_time - start_time
                except TimeoutError:
                    print(f'测试节点{i}超时')
                except Exception as e:
                    print(f'出现未知错误,测试节点{i}失败')
                    print(f'错误为: {e}')
        recommend_link_index = 1
        for i in range(1, 6):
            print(
                f'链路{i}时延:{link_time[i]:>2.2f}{" (30为超时或失败)" if link_time[i] >= 30 else ""}')
            if link_time[i] < link_time[recommend_link_index]:
                recommend_link_index = i
        _link_index = recommend_link_index
        if link_time[_link_index] >= 30:
            print('所有链路延时均超过30s')
            # write_file()
        print(f'已将默认链路设置为 link{_link_index}')
        return recommend_link_index

    @staticmethod
    async def bind_account(qq: int, playername: str):
        cx = sqlite3.connect(DB_PATH)
        cursor = cx.cursor()
        fk_pid = cursor.execute("select id from qhplayer where playername = ?", (playername,)).fetchall()
        if len(fk_pid) > 0:
            fk_pid = fk_pid[0][0]
        else:
            cursor.close()
            cx.close()
            return await messagechain_builder(text="绑定失败,请先用qhpt查询玩家")
        result = cursor.execute("select id from accountbind where qq = ?", (qq,)).fetchall()
        if len(result) > 0:
            cursor.execute("update accountbind set player_fkid = ? where qq = ?", (fk_pid, qq))
        else:
            cursor.execute("insert into accountbind(qq,player_fkid) values(?,?)", (qq, fk_pid))
        cx.commit()
        cursor.close()
        cx.close()
        return await messagechain_builder(text="绑定成功")

    @staticmethod
    async def bind_operation(qq: int, opertaion: str, searchtype=3, other=None) -> list:
        player_info = link_account(qq)
        if searchtype is None:
            searchtype = 3
        if not player_info.get('bind'):
            return await messagechain_builder(at=qq, text="未绑定账号,请先绑定账号")
        if opertaion == 'pt':
            return await query_pt_byid(player_info.get('account'))
        elif opertaion == 'yb':
            return await get_monthreport_byid(player_info, month=other, selecttype=searchtype, qq=qq)
        elif opertaion == 'info':
            return await get_playerinfo_byid(player_info, searchtype, model=other, qq=qq)
        elif opertaion == 'paipu':
            return await get_player_records_byid(player_info, searchtype, model=other, qq=qq)
        else:
            return await messagechain_builder(at=qq, text="无此方法")

   

def link_account(qq: int) -> dict:
    cx = sqlite3.connect(DB_PATH)
    cursor = cx.cursor()
    result = dict(bind=False, account=None, playername=None, msg='未绑定账号')
    player_info = cursor.execute(
        "select qhplayer.playerid,qhplayer.playername "
        "from accountbind left join qhplayer on qhplayer.id = accountbind.player_fkid "
        "where qq = ?",
        (qq,),
    ).fetchall()
    if len(player_info) > 0:
        player_info = player_info[0]
        if player_info[0] != 0:
            result['bind'] = True
            result['account'] = player_info[0]
            result['playername'] = player_info[1]
    cursor.close()
    cx.close()
    return result


def get_playerid(playername: str):
    cx = sqlite3.connect(DB_PATH)
    cursor = cx.cursor()
    cursor.execute("select playerid from qhplayer where playername = ?", (playername,))
    playerid = cursor.fetchall()
    cursor.close()
    cx.close()
    if len(playerid) > 0:
        return playerid[0][0]
    return None


async def asyqhpt(username: str, selecttype: str = None, selectindex: int = None) -> dict:
    """异步的qhpt"""
    muti3 = False
    muti4 = False
    if not selectindex:
        selectindex = 0
    urlp3 = get_qhpturl(username, 3)
    urlp4 = get_qhpturl(username, 4)
    if selecttype:
        return dict()
        # if selecttype == "3":
        #     url = urlp3
        #     typename = "三麻"
        # else:
        #     url = urlp4
        #     typename = "四麻"
        # try:
        #     async with ClientSession(connector=TCPConnector(ssl=False, limit=_config.get('query_limit),10, timeout=aiotimeout,
        #                                      headers={'User-Agent': random.choice(user_agent_list)}) as session:
        #         async with session.get(url) as response:
        #             if response.status == 503:
        #                 return dict(error=True, offline=True)
        #             playerinfo = await response.json()
        # except TimeoutError as e:
        #     print(f"qhpt查询超时,{e}")
        #     return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        # except httpx.ConnectError as _e:
        #     print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
        #     return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        # if len(playerinfo) == 0:
        #     print("该玩家不存在或未进行金之间及以上对局")
        #     return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        # elif len(playerinfo) < selectindex:
        #     print(f"序号有误，共查询到{len(playerinfo)}名玩家,序号最大值为{len(playerinfo) - 1}")
        #     return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        # else:
        #     playerinfo = playerinfo[selectindex]
        #     if type(playerinfo) == dict:
        #         playerid = playerinfo['id']
        #         playername = playerinfo['nickname']
        #         prtmsg = f"玩家名: {playername}"
        #         levelinfo = playerinfo.get("level")
        #         level = levelinfo.get("id")
        #         score = int(levelinfo.get("score")) + int(levelinfo.get("delta"))
        #         prtmsg += levelswitch(level, score, typename)
        #         cx = sqlite3.connect(DB_PATH)
        #         cursor = cx.cursor()
        #         cursor.execute(
        #             f"select * from qhplayer where playername = '{username}'")
        #         if len(cursor.fetchall()) == 0:
        #             cursor.execute(
        #                 "insert into qhplayer(playerid,playername) values(?,?)", (playerid, username))
        #         else:
        #             cursor.execute(
        #                 f"update qhplayer set playerid = {playerid} where playername = '{username}'")
        #         cx.commit()
        #         cx.close()
        #         return prtmsg

    else:
        try:
            async with ClientSession(
                    connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                    headers={'User-Agent': random.choice(user_agent_list)}) as session:
                async with session.get(urlp3) as response:
                    if response.status == 503:
                        return dict(error=True, offline=True)
                    pl3 = await response.json()
                async with session.get(urlp4) as response:
                    if response.status == 503:
                        return dict(error=True, offline=True)
                    pl4 = await response.json()

        except TimeoutError as e:
            if not _config.get('silence_CLI', False):
                print(f"qhpt查询超时,{e}")
            return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        except httpx.ConnectError as _e:
            print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
            return dict(error=True, muti3=muti3, muti4=muti4, offline=False)
        pl3info = None
        pl4info = None
        if len(pl3) > 0:
            if len(pl3) > 1:
                muti3 = True
            for _pl in pl3:
                if int(_pl.get('level', dict()).get('id', 20001)) > 20000:
                    pl3info = _pl
                    break
            # else:
            #     pl3info = None
        if len(pl4) > 0:
            if len(pl4) > 1:
                muti4 = True
            pl4info = None
            for _pl in pl4:
                if int(_pl.get('level', dict()).get('id', 0)) < 20000:
                    pl4info = _pl
                    break
            # else:
            #     pl4 = None
        playerid = None
        playername = None
        if pl3info:
            # if pl3info['nickname'] == username:
            playerid = pl3info['id']
            playername = pl3info['nickname']
        elif pl4info:
            # if pl4info['nickname'] == username :
            playerid = pl4info['id']
            playername = pl4info['nickname']
        else:
            muti3 = False
            muti4 = False
        print(pl4)
        print(pl3)
        return dict(pl3=pl3info, pl4=pl4info, playerid=playerid, playername=playername, error=False, muti3=muti3,
                    muti4=muti4,
                    offline=False)


async def getmatchresult(playeridlist, nowtime) -> list:
    """异步查询雀魂对局"""
    contentlist = []
    if len(playeridlist) >= 25:
        timeout = ClientTimeout(total=15)
    else:
        timeout = ClientTimeout(total=60)
    async with ClientSession(connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)),
                                     timeout=timeout,
                                     headers={'User-Agent': random.choice(user_agent_list)}) as session:
        for playerid in playeridlist:
            try:
                #  url =f"https://ak-data-1.sapk.ch/api/v2/pl3/player_records/{playerid}/{nowtime}/1262304000000?limit=1&mode=21,22,23,24,25,26&descending=true"
                url = get_player_records_url(playerid, 3, nowtime, total=1)
                async with session.get(url) as response:
                    text = await response.json()
                    if len(text) > 0:
                        contentlist.append(
                            dict(playerid=playerid, content=text[0]))
                    else:
                        contentlist.append(
                            dict(playerid=playerid, content=text))
                url = get_player_records_url(playerid, 4, nowtime, total=1)
                async with session.get(url) as response:
                    text = await response.json()
                    if len(text) > 0:
                        contentlist.append(
                            dict(playerid=playerid, content=text[0]))
                    else:
                        contentlist.append(
                            dict(playerid=playerid, content=text))
            except TimeoutError as e:
                logging.getLogger().exception(e)
            except Exception as e:
                logging.getLogger().exception(e)
    return contentlist

    # 返回一个 list，内容为 [{groupid:groupid,msg:msg}]


def jiexi(paipu: dict, playerid: int) -> list:
    """
    对局信息解析

    Args:
        paipu: 查到的所有牌谱
        playerid: 筛选的玩家id

    Returns: 合法的最新对局

    """
    hasNewPaipu = False
    # paipuInfo = "检测到新的对局信息:\n"
    paipuInfo = ""
    cx = sqlite3.connect(DB_PATH)

    cursor = cx.cursor()
    cx.commit()
    allpaipuinfo = []
    for item in paipu['p4']:
        paipuurl = f'https://game.maj-soul.net/1/?paipu={item["uuid"]}'
        startTime = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(item["startTime"]))
        endTime = time.strftime('%Y-%m-%d %H:%M:%S',
                                time.localtime(item["endTime"]))
        players = item['players']
        try:
            cursor.execute(
                "insert into paipu(uuid,watchid,startTime,endTime,player1,player2,player3,player4) values(?,?,?,?,?,?,?,?)",
                (item['uuid'], playerid, startTime, endTime, f"{players[0]['nickname']}:{players[0]['score']}",
                 f"{players[1]['nickname']}:{players[1]['score']}", f"{players[2]['nickname']}:{players[2]['score']}",
                 f"{players[3]['nickname']}:{players[3]['score']}"))
            cx.commit()
            paipuInfo += f"牌谱链接 : {paipuurl}\n"
            paipuInfo += f"开始时间: {startTime}\n结束时间: {endTime}\n对局玩家:\n"
            for info in players:
                paipuInfo += f"{info['nickname']}:{info['score']} ({info['gradingScore']})\n"
            hasNewPaipu = True
        except sqlite3.IntegrityError:
            # print(f"存在uuid={item['uuid']}的记录")
            pass
    for item in paipu['p3']:
        paipuurl = f'https://game.maj-soul.net/1/?paipu={item["uuid"]}'
        startTime = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(item["startTime"]))
        endTime = time.strftime('%Y-%m-%d %H:%M:%S',
                                time.localtime(item["endTime"]))
        players = item['players']

        try:
            cursor.execute(
                "insert into paipu(uuid,watchid,startTime,endTime,player1,player2,player3,player4) values(?,?,?,?,?,?,?,?)",
                (item['uuid'], playerid, startTime, endTime, f"{players[0]['nickname']}:{players[0]['score']}",
                 f"{players[1]['nickname']}:{players[1]['score']}", f"{players[2]['nickname']}:{players[2]['score']}",
                 "Null"))
            cx.commit()
            paipuInfo += f"牌谱链接 : {paipuurl}\n"
            paipuInfo += f"开始时间: {startTime}\n结束时间: {endTime}\n对局玩家:\n"
            for info in players:
                paipuInfo += f"{info['nickname']}:{info['score']} ({info['gradingScore']})\n"
            hasNewPaipu = True
        except sqlite3.IntegrityError:
            # print(f"存在uuid={item['uuid']}的记录")
            pass
    allpaipuinfo.append(
        dict(paipuInfo=paipuInfo, hasNewPaipu=hasNewPaipu))
    cursor.close()
    cx.close()
    return allpaipuinfo


def levelswitch(level, score, select_type='三麻', separator=':', space_length=4) -> str:
    """
    玩家段位解析

    Args:
        level: 段位
        score: 分数
        select_type: 三麻或者四麻
        separator: 分隔符
        space_length: 间距长度

    Returns:

    """
    space = ""
    if space_length == 4:
        space = '    '
    else:
        for _ in range(space_length):
            space += " "
    stage_level = int(str(level)[2:3]) - 3
    # print(f'stage:{select_type}:{stage_level}')
    score_level = int(str(level)[4:5]) - 1
    # print(f'score:{select_type}:{score_level}')
    maxscore = 2000
    msg = ""
    if stage_level < 3:
        maxscore = levellist[stage_level][score_level]

    # 升段
    if score >= maxscore:
        score_level += 1
        if stage_level < 4:
            if score_level > 2:
                score_level = 0
                stage_level += 1
        if stage_level == 3:
            stage_level += 1
        if stage_level < 3:
            maxscore = levellist[stage_level][score_level]
        else:
            maxscore = 2000
        score = maxscore // 2

    # 掉段
    if score < 0:
        score_level -= 1
        if score_level < 0:
            score_level = 2
            stage_level -= 1
        if stage_level == 3:
            stage_level -= 1
        if stage_level < 3:
            maxscore = levellist[stage_level][score_level]
        else:
            maxscore = 2000
        score = maxscore // 2

    if stage_level < 0:
        # msg += type + "段位:  雀士"
        msg += select_type + separator + "  雀士"
    elif stage_level < 4:
        # msg += type + "段位: " + prtlevelmsg(stage_level, score_level) + " \t" + type + "分数: " + str(
        #     score) + "/" + str(maxscore)
        msg += select_type + separator + prtlevelmsg(stage_level, score_level) + space + "[" + str(
            score) + "/" + str(maxscore) + "]"
    else:
        # msg += type + "段位: " + prtlevelmsg(stage_level, score_level) + " \t" + type + "分数: " + str(
        #     score / 100) + "/" + str(maxscore / 100)
        msg += select_type + separator + prtlevelmsg(stage_level, score_level) + space + "[" + str(
            score / 100) + "/" + str(maxscore / 100) + "]"
    return msg


def prtlevelmsg(stagelevel, scorelevel):
    """
    段位显示

    Args:
        stagelevel: 大段
        scorelevel: 小分

    Returns:    例:三麻魂9

    """
    msg = ""
    if stagelevel == 0:
        msg += "杰"
    elif stagelevel == 1:
        msg += "豪"
    elif stagelevel == 2:
        msg += "圣"
    else:
        msg += "魂"

    msg += str(scorelevel + 1)
    if stagelevel < 0:
        msg = "雀士"
    return msg


# def mergeimg(imgurls: list) -> Image:
#     for url in imgurls:
#         img = Image.open(f"./plugin/Majsoul/Images/{url}")
#     return


def getrank(playerinfo: dict):
    """
    自定义对局信息(牌谱)的sort方法
    Args:
        playerinfo: 牌谱中的玩家信息

    Returns:

    """
    return playerinfo['score']


def forwardmessage(msglist: list) -> list:
    """将结果封装为便于转发的消息链"""
    messageChainList = []
    # cmdopt = []
    cx = sqlite3.connect(DB_PATH)
    cursor = cx.cursor()
    for item in msglist:
        groupids = []
        cursor.execute(
            "select groupid,playername from group2player where playerid = ? and iswatching = 1",
            (item['playerid'],),
        )
        results = cursor.fetchall()
        if not results:
            continue
        for g in results:
            groupids.append(g[0])
        messageChainList.append(
            dict(groups=groupids, msg=item['msg'], playerid=item['playerid'], playername=results[0][1],
                 link=item['link']))
    cursor.close()
    cx.close()
    return messageChainList


async def get_player_records_byid(playerid, selecttype: int | str = '4', counts=5) -> list:
    """
    通过玩家id获取对局记录

    Args:
        playerid: 玩家id
        selecttype: 查询类型
        counts: 数量

    Returns:

    """
    if counts is None:
        counts = 5
    if selecttype is None:
        selecttype = 4
    async with ClientSession(
            connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
            headers={'User-Agent': random.choice(user_agent_list)}) as session:
        async with session.get(get_paipuurl(playerid, selecttype, counts)) as response:
            text = await response.json()
    return text


'''
{'_id': '8R4hmIkLAfZ', 
'modeId': 12, 
'uuid': '220331-59b46bb1-1177-4843-9f0b-6125fe9ae5ba', 
'startTime': 1648656045, 'endTime': 1648658431, 
'players': [
{'accountId': 73438605, 'nickname': 'ドルオーラ', 'level': 10401, 'score': 27200, 'gradingScore': 63}, 
{'accountId': 8560870, 'nickname': '云游水', 'level': 10401, 'score': 4600, 'gradingScore': -200}, 
{'accountId': 71424576, 'nickname': 'シュウ4', 'level': 10501, 'score': 51200, 'gradingScore': 152},
 {'accountId': 12728158, 'nickname': '兔子甲', 'level': 10401, 'score': 17000, 'gradingScore': -13}
 ]}
'''


def msganalysis(infos: list) -> list:
    """消息解析"""

    def get_score(e):
        return e['score']

    content = []
    cx = sqlite3.connect(DB_PATH)
    cursor = cx.cursor()
    for item in infos:
        msgitem = item['content']
        if len(msgitem) == 0:
            continue
        paipuInfo = ""
        broadcast_type = _config.get('broadcast', 'image').lower()
        if broadcast_type in ['str', 'txt', 'text']:
            paipuurl = f'https://game.maj-soul.net/1/?paipu={msgitem["uuid"]}'
        elif broadcast_type in ['img', 'image']:
            paipuurl = f'{msgitem["uuid"]}'
        else:
            paipuurl = ''
        startTime = time.strftime(
            '%Y-%m-%d %H:%M:%S', time.localtime(msgitem["startTime"]))
        endTime = time.strftime('%Y-%m-%d %H:%M:%S',
                                time.localtime(msgitem["endTime"]))
        players: list = msgitem['players']
        players.sort(key=get_score, reverse=True)
        try:
            if len(players) == 3:
                cursor.execute(
                    "insert into paipu(uuid,watchid,startTime,endTime,player1,player2,player3,player4) values(?,?,?,?,?,?,?,?)",
                    (msgitem['uuid'], item['playerid'], startTime, endTime,
                     f"{players[0]['nickname']}:{players[0]['score']}",
                     f"{players[1]['nickname']}:{players[1]['score']}",
                     f"{players[2]['nickname']}:{players[2]['score']}",
                     "Null"))
            else:
                cursor.execute(
                    "insert into paipu(uuid,watchid,startTime,endTime,player1,player2,player3,player4) values(?,?,?,?,?,?,?,?)",
                    (msgitem['uuid'], item['playerid'], startTime, endTime,
                     f"{players[0]['nickname']}:{players[0]['score']}",
                     f"{players[1]['nickname']}:{players[1]['score']}",
                     f"{players[2]['nickname']}:{players[2]['score']}",
                     f"{players[3]['nickname']}:{players[3]['score']}"))
            cx.commit()
            paipuInfo += f"{paipuurl}\n"
            paipuInfo += f"{startTime} ~ {endTime}\n对局玩家:\n"
            for player in players:
                paipuInfo += f"{player['nickname']}:{player['score']} ({player['gradingScore']})\n"
            content.append(dict(playerid=item['playerid'], msg=paipuInfo,
                                link=f'https://game.maj-soul.net/1/?paipu={msgitem["uuid"]}'))
        except sqlite3.IntegrityError:
            # print(f"存在uuid={item['uuid']}的记录")
            pass
    return forwardmessage(content)


async def query_pt_byid(playerid: int, searchtype: str | list | None = None, qq: int = None):
    """
    通过数字id查询玩家pt
    Args:
        playerid: 玩家牌谱屋的数字id
        searchtype: 查询类型
        qq: 请求指令的QQ号

    Returns:

    """
    if not searchtype:
        searchtype = [3, 4]
    """
    {"count":847,
    "level":{"id":20401,"score":455,"delta":2},
    "max_level":{"id":20401,"score":1474,"delta":134},
    "rank_rates":[0.34946871310507677,0.3010625737898465,0.34946871310507677],
    "rank_avg_score":[55925,34044,16675],
    "avg_rank":2,
    "negative_rate":0.04014167650531287,
    "id":1111111,
    "nickname":"xxxxx",
    "played_modes":[22,23,21]}
    """
    if isinstance(searchtype, str):
        searchtype = [searchtype]
    async with ClientSession(
            connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
            headers={'User-Agent': random.choice(user_agent_list)}) as session:
        msg = ""
        for stype in searchtype:
            url = get_pturl_by_pid(playerid, stype)
            async with session.get(url) as response:
                if response.status == 503:
                    if not _config.get('silence_CLI', False):
                        print('牌谱屋似乎离线了')
                    return await messagechain_builder(text="牌谱屋似乎离线了~")
                content = await response.json()
            maxlevel_info = content.get('max_level', None)
            nowlevel_info = content.get('level', None)
            if maxlevel_info:
                playername = content.get("nickname")
                max_level = maxlevel_info.get("id")
                max_score = int(maxlevel_info.get("score")) + int(maxlevel_info.get("delta"))
                now_level = nowlevel_info.get("id")
                now_score = int(nowlevel_info.get("score")) + int(nowlevel_info.get("delta"))
                if stype in ['3', 3, '三麻', '三']:
                    msg += "\n三麻:\n最高" + levelswitch(max_level, max_score, '')
                    msg += "\n当前" + levelswitch(now_level, now_score, '')
                else:
                    msg += "\n四麻:\n最高" + levelswitch(max_level, max_score, '')
                    msg += "\n当前" + levelswitch(now_level, now_score, '')
                msg += "\n"
        msg = playername + msg[:-1]
    if qq:
        return await messagechain_builder(at=qq, text=msg)
    if _config.get('broadcast', 'image').lower() in ['text', 'txt', 'str']:
        return await messagechain_builder(text=msg)
    return await messagechain_builder(imgbase64=text_to_imagebytes(text=msg,needtobase64=True))


async def get_monthreport_byid(player_info: dict, selecttype: str | int = 4, month: str = None,
                               qq: int = None):
    """
    通过玩家id查询月报

    Args:
        player_info: 数据库查询到玩家信息
        selecttype: 查询类型,仅接受int和str
        month: 查询月份
        qq: 请求指令的QQ号

    Returns:

    """
    if selecttype is None:
        selecttype = "3"
    matchtype = f'{"三" if selecttype in [3, "3"] else "四"}麻'
    playerid = player_info.get('account')
    playername = player_info.get('playername')
    if not month:
        nextmontht = int(time.time() * 1000)
        month = "最近一个月"
        paipumsg = f"{playername} {matchtype} {month} 月报:\n"
    else:
        if re.match(r"\d{2,4}-\d{1,2}", month):
            _y, _m = month.split('-')
            if 1 > int(_m) or int(_m) > 12:
                return await messagechain_builder(text="请输入正确的时间")
            paipumsg = f"{playername} {matchtype} {month} 月报:\n"
            if _m == "12":
                month = f"{int(_y) + 1}-1"
            else:
                month = f"{_y}-{int(_m) + 1}"
            nextmontht = int(time.mktime(time.strptime(month, '%Y-%m')) * 1000)
        else:
            return await messagechain_builder(text="请输入正确的时间")
    selectmontht = nextmontht - 2592000 * 1000
    msg = ""
    rankdict = {"1": 0, "2": 0, "3": 0, "4": 0, "fly": 0}
    playerslist = []
    try:
        async with ClientSession(
                connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                headers={'User-Agent': random.choice(user_agent_list)}) as session:
            url = get_player_records_url(playerid, selecttype, nextmontht, selectmontht)
            async with session.get(url) as response:
                if response.status == 503:
                    return await messagechain_builder(text='牌谱屋似乎离线了')
                paipuresponse = await response.json()
            url = get_player_extended_stats_url(playerid, selecttype, end_time=nextmontht, start_time=selectmontht)
            async with session.get(url) as response:
                if response.status == 503:
                    return await messagechain_builder(text='牌谱屋似乎离线了')
                inforesponse: dict = await response.json()
            if inforesponse.get('error', None) == 'id_not_found':
                return await messagechain_builder(at=qq, text=f'你{matchtype}似乎没有打过金之间呢')
            if len(paipuresponse) == 0:
                return await messagechain_builder(at=qq, text=f'你这个月似乎没有进行过{matchtype}的对局呢')
    except TimeoutError:
        return await messagechain_builder(at=qq, text="查询超时, 请稍后再试")
    paipumsg += f"总对局数: {len(paipuresponse)}\n其中"
    ptchange = 0
    for players in paipuresponse:
        temp = players['players']
        temp.sort(key=getrank)
        playerslist.append(temp)
    for playerrank in playerslist:
        rank = len(playerrank)
        for player in playerrank:
            if player['nickname'] == playername:
                ptchange += player['gradingScore']
                rankdict[f"{rank}"] += 1
                if player['score'] < 0:
                    rankdict['fly'] += 1
                break
            rank = rank - 1
    averagerank = (rankdict['1'] + rankdict['2'] * 2 +
                   rankdict['3'] * 3 + rankdict['4'] * 4) / len(paipuresponse)
    if rankdict['1'] + rankdict['2'] + rankdict['3'] + rankdict['4'] < len(paipuresponse):
        paipumsg += "玩家名绑定的玩家名似乎输入有误,请尝试用qhpt 3/4 绑定正确的玩家名\n"
    else:
        if selecttype == "4":
            paipumsg += f"{rankdict['1']}次①位,{rankdict['2']}次②位,{rankdict['3']}次③位,{rankdict['4']}次④位"
        else:
            paipumsg += f"{rankdict['1']}次①位,{rankdict['2']}次②位,{rankdict['3']}次③位"
        if rankdict['fly'] > 0:
            paipumsg += f",被飞了{rankdict['fly']}次"
        paipumsg += f",平均顺位:{averagerank:1.2f}\nPT总得失: {ptchange}\n\n"
    msg += paipumsg
    infomsg = f" 立直率: {inforesponse.get('立直率', None) * 100 if inforesponse.get('立直率', None) else 0:2.2f}%\t"
    infomsg += f" 副露率: {inforesponse.get('副露率', None) * 100 if inforesponse.get('副露率', None) else 0:2.2f}%\t"
    infomsg += f" 和牌率: {inforesponse.get('和牌率', None) * 100 if inforesponse.get('和牌率', None) else 0:2.2f}%\n"
    infomsg += f" 放铳率: {inforesponse.get('放铳率', None) * 100 if inforesponse.get('放铳率', None) else 0:2.2f}% "
    if inforesponse.get('默听率', None):
        infomsg += f"\t 默听率: {inforesponse.get('默听率', 0) * 100 :2.2f}%\n"
    else:
        infomsg += '\t'
    infomsg += f" 平均打点: {inforesponse.get('平均打点') if inforesponse.get('平均打点') else 0}\t 平均铳点 : {inforesponse.get('平均铳点') if inforesponse.get('平均铳点') else 0}"
    msg += infomsg
    return await messagechain_builder(imgbase64=text_to_imagebytes(fontsize=36, text=msg, needtobase64=True))


async def get_playerinfo_byid(player_info: dict, selecttype: str | int = 4, select_level: str = None, model=None,
                              qq: int = None):
    """
    通过id查询玩家详情
    Args:
        player_info: 数据库查询到玩家信息
        selecttype: 查询类型
        model: 模式
        qq:

    Returns:

    """
    if model is None:
        model = '基本'
    if selecttype is None:
        selecttype = 4
    if model not in ['基本', '更多', '立直', '血统', 'all']:
        return await messagechain_builder(text="参数输入有误哦，可用的参数为'基本'、'更多'、'立直'、'血统'、'all'")
    playerid = player_info.get("account")
    playername = player_info.get("playername")
    rule = "三麻"
    if select_level and select_level in _match_level_name:
        url = get_player_extended_stats_url(playerid, selecttype, mode=select_level)
    else:
        url = get_player_extended_stats_url(playerid, selecttype)
    if selecttype == "4":
        rule = "四麻"
    try:
        async with ClientSession(
                connector=TCPConnector(ssl=False, limit=_config.get('query_limit', 10)), timeout=aiotimeout,
                headers={'User-Agent': random.choice(user_agent_list)}) as session:
            async with session.get(url) as response:
                if response.status == 503:
                    if not _config.get('silence_CLI', False):
                        print('牌谱屋似乎离线了')
                    return await messagechain_builder(text="牌谱屋似乎离线了~")
                content = await response.json()
    except TimeoutError as e:
        if not _config.get('silence_CLI', False):
            print(f"查询超时:\t{e}\n")
        return await messagechain_builder(text="查询超时,请稍后再试")
    except httpx.ConnectError as _e:
        if not _config.get('silence_CLI', False):
            print(f"发生了意外的错误,类别为httpx.ConnectError,可能的原因是连接达到上限,可以尝试关闭代理:\n{_e}")
        return await messagechain_builder(text="查询超时,请稍后再试")
    if content.get('error', False):
        return await messagechain_builder(text='未找到该玩家在这个场次的的对局')
    msg = f" 以下是玩家 {playername} 的{rule}数据:\n"
    for (k, v) in content.items():
        if not isinstance(v, (list, dict)):
            if str(k) in ["id", "count"]:
                continue
            if model in ['基本', '更多', '血统', '立直']:
                if str(k) in infomodel.get(model):
                    if isinstance(v, float):
                        if str(k).endswith('率'):
                            msg += f"{k:<12} : {v * 100:2.2f}%\n"
                        else:
                            msg += f"{k:<12} : {v:2.2f}\n"
                    else:
                        msg += f"{k:<12} : {v if v else 0}\n"
            elif model == 'all':
                if isinstance(v, float):
                    if str(k).endswith('率'):
                        msg += f"{k:<12} : {v * 100:2.2f}%\n"
                    else:
                        msg += f"{k:<12} : {v:2.2f}\n"
                else:
                    msg += f"{k:<12} : {v if v else 0}\n"
    _broadcast_type = _config.get('broadcast', 'image').lower()
    if _broadcast_type in ['txt', 'text', 'str']:
        return await messagechain_builder(at=qq, text=msg)
    else:
        return await messagechain_builder(at=qq, imgbase64=text_to_imagebytes(fontsize=36, text=msg, needtobase64=True))


async def get_playerpaipu_byid(player_info: dict, selecttype: str | int = 4, counts=5,
                               qq: int = None):
    """
    通过id查询玩家详情
    Args:
        player_info: 数据库查询到玩家信息
        selecttype: 查询类型
        counts: 模式
        qq:

    Returns:

    """
    playername = player_info.get("playername")
    playerid = player_info.get("account")
    if counts is None:
        counts = '5'
    counts = int(counts)
    if selecttype is None:
        selecttype = '4'
    if counts < 0 or counts > 10:
        return await messagechain_builder(text="牌局数量有误，最多支持10场牌局")
    if selecttype not in ['3', '4', 3, 4]:
        return await messagechain_builder(text="牌局参数有误，请输入 3 或 4")
    ptupdate = 0
    ERROR = False

    paipuInfo = f"最近{counts}场对局信息如下："
    _paipu_link = ''
    try:
        content = await get_player_records_byid(playerid, selecttype, counts)
        for item in content:
            paipuuid = f'{item["uuid"]}'
            startTime = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(item["startTime"]))
            endTime = time.strftime('%Y-%m-%d %H:%M:%S',
                                    time.localtime(item["endTime"]))
            players = item['players']
            _broadcast_type = _config.get('broadcast', 'image').lower()
            if _broadcast_type in ['txt', 'text', 'str']:
                paipuInfo += f"\n牌谱连接: https://game.maj-soul.net/1/?paipu={paipuuid}\n"
                _paipu_link += f"https://game.maj-soul.net/1/?paipu={paipuuid}\n"
            else:
                paipuInfo += f"\n牌谱UID: {paipuuid}\n"
            paipuInfo += f"开始时间: {startTime}\n结束时间: {endTime}\n对局玩家:\n"
            for player in players:
                if player['nickname'].strip() == playername.strip():
                    ptupdate += int(player['gradingScore'])
                paipuInfo += f"{player['nickname']} : {player['score']} ({player['gradingScore']})\n"
            paipuInfo += "\n"
        paipuInfo += f"\n总PT变化 : {ptupdate}"
    except TimeoutError as e:
        if not _config.get('silence_CLI', False):
            print(e)
        ERROR = True
        paipuInfo = '牌谱查询超时,请稍后再试'
    result = await messagechain_builder(text=paipuInfo)
    if not ERROR:
        _broadcast_type = _config.get('broadcast', 'image').lower()
        if _broadcast_type in ['txt', 'text', 'str']:
            return await messagechain_builder(at=qq, text=paipuInfo)
        elif _broadcast_type in ['mix', 'mixed']:
            return await messagechain_builder(at=qq, text=_paipu_link,
                                              imgbase64=text_to_imagebytes(fontsize=36, text=paipuInfo, needtobase64=True))
        else:
            # text_to_image(fontsize=36, path=f"MajsoulInfo/qhpt{username}.png", text=prtmsg)
            return await messagechain_builder(at=qq,
                                              imgbase64=text_to_imagebytes(fontsize=36, text=paipuInfo, needtobase64=True))
        # result['img64'] = text_to_image(fontsize=36, text=paipuInfo, needtobase64=True)
    return result


def db_init():
    cx = sqlite3.connect(DB_PATH)
    cursor = cx.cursor()
    cursor.execute("create table if not exists paipu("
                   "id integer primary key,"
                   "uuid varchar(50) UNIQUE,"
                   "watchid integer,"
                   "startTime varchar(50),"
                   "endTime varchar(50),"
                   "player1 varcher(50),"
                   "player2 varcher(50),"
                   "player3 varcher(50),"
                   "player4 varcher(50)"
                   ")")
    cursor.execute('create table if not exists watchedplayer ('
                   'id integer primary key,'
                   'watchedgroupcount integer not null default 0,'
                   'playerid integer,'
                   'playername varchar(50) UNIQUE)')
    cursor.execute("create table if not exists QQgroup("
                   "id integer primary key ,"
                   "groupid integer UNIQUE)")
    cursor.execute("create table if not exists group2player("
                   "id integer primary key,"
                   "groupid integer,"
                   "playerid integer,"
                   "playername varchar(50),"
                   'iswatching integer not null default 1,'
                   "UNIQUE(groupid,playerid) ON CONFLICT REPLACE)")
    cursor.execute('create table if not exists qhplayer ('
                   'id integer primary key,'
                   'playerid integer,'
                   'playername varchar(50) UNIQUE)')
    cursor.execute("create table IF NOT EXISTS drawcards("
                   "id integer primary key,"
                   "userid int UNIQUE,"
                   "drawcount int,"
                   "lastdraw varchar(50)"
                   ")")
    cursor.execute("create table if not exists playerdrawcard("
                   "id integer primary key,"
                   "userid integer not null,"
                   "drawtime varchar(50) not null,"
                   "itemlevel int not null,"
                   "itemname TEXT not null"
                   ")")
    cursor.execute("create table if not exists tagnames("
                   "id integer primary key,"
                   "tagname text not null,"
                   "userid integer not null,"
                   "gpid integer not null,"
                   "constraint gp_nickname "
                   "foreign key (gpid) "
                   "references group2player(id)"
                   ")")

    cursor.execute("create table if not exists accountbind("
                   "id integer primary key,"
                   "qq integer not null,"
                   "player_fkid integer not null,"
                   "constraint fk_pid "
                   "foreign key (player_fkid) "
                   "references qhplayer(id)"
                   ")")

    cursor.execute("create view if not exists groupwatches as "
                   "select groupid,"
                   "group_concat(playername) as watchedplayers,"
                   "count(groupid) as watchnums "
                   "from group2player "
                   "where iswatching = 1 "
                   "group by groupid")
    cursor.execute("create view if not exists watchedplayersview as "
                   "select playername,playerid, "
                   "count(groupid) as watchedgroupcount "
                   "from group2player "
                   "where iswatching = 1 "
                   "group by playername")
    cursor.execute("create view if not exists tagnameview as "
                   "select tagname,playername,groupid "
                   "from tagnames as tg join group2player as gp "
                   "where tg.gpid = gp.id "
                   "and gp.iswatching = 1")

    cx.commit()
    cursor.close()
    cx.close()

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
db_init()

# majsoul = MajsoulQuery()
