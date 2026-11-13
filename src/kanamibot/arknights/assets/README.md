# 方舟本地资料

`operators.json` 保存可获取干员的名称、ID、阵营和异格关系；阵营依次取游戏数据的
小队、组织、国家字段。异格来自 `char_meta_table.spCharGroups`，组内首项为原干员；
多个异格由用户选择，不按名称猜测。

`nicknames.json` 是人工筛选的社群绰号初始表，当前包含 249 个绰号。它与运行时的
`data/arknights/nicknames.json` 分开保存，更新初始资料不会覆盖群聊添加和删除的记录。
运行时文件包含自定义绰号和已删除绰号；删除项也会阻止重新使用后端同步的同名绰号。

资料来源（各 JSON 同时记录抓取日期和源文件 SHA-256）：

- 干员、阵营与异格关系：[ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData/tree/master/zh_CN/gamedata/excel)。
- 绰号：[AmiyaBot 社群全局替换表](http://106.52.139.57:8000/replace/getGlobalReplace)，
  与 [Amiya MCP](https://github.com/AmiyaBot/Amiya-Bot-mcp-server) 的绰号来源相同。
  仅提取有效干员对应的名称和绰号，不保存 QQ 号、群号等字段。
- “42”对照：[游侠手游《明日方舟干员外号汇总》](https://m.ali213.net/news/gl2408/1479047.html)。

初始表保留常见缩写、昵称和部分常用真名，排除正式名冲突、歧义替换和部分不适合
作为查询名的玩笑。更新脚本使用明确的筛选名单，新增种子绰号须审阅后加入。
这是一份可扩充的初始表，不代表全部社群用法。

更新公开资料（不修改运行时文件）：

```powershell
python scripts/update_arknights_records.py
```
