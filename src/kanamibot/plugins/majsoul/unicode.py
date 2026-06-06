tiles = {
    "1m": 0x1F007, # 一万
    "2m": 0x1F008, # 二万
    "3m": 0x1F009, # 三万
    "4m": 0x1F00A, # 四万
    "5m": 0x1F00B, # 五万
    "0m": 0x1F00B, # 五万
    "6m": 0x1F00C, # 六万
    "7m": 0x1F00D, # 七万
    "8m": 0x1F00E, # 八万
    "9m": 0x1F00F, # 九万
    "1s": 0x1F010, # 一筒
    "2s": 0x1F011, # 二筒
    "3s": 0x1F012, # 三筒
    "4s": 0x1F013, # 四筒
    "5s": 0x1F014, # 五筒
    "0s": 0x1F014, # 五筒
    "6s": 0x1F015, # 六筒
    "7s": 0x1F016, # 七筒
    "8s": 0x1F017, # 八筒
    "9s": 0x1F018, # 九筒
    "1p": 0x1F019, # 一条
    "2p": 0x1F01A, # 二条
    "3p": 0x1F01B, # 三条
    "4p": 0x1F01C, # 四条
    "5p": 0x1F01D, # 五条
    "0p": 0x1F01D, # 五条
    "6p": 0x1F01E, # 六条
    "7p": 0x1F01F, # 七条
    "8p": 0x1F020, # 八条
    "9p": 0x1F021, # 九条
    "1z": 0x1F000, # 东风
    "2z": 0x1F001, # 南风
    "3z": 0x1F002, # 西风
    "4z": 0x1F003, # 北风
    "5z": 0x1F004, # 红中
    "6z": 0x1F005, # 发财
    "7z": 0x1F006, # 白板
    "0z": 0x1F02B, # 牌背
}

def restore(input):
    # 初始化一个空字符串来存储还原后的字符串
    output = ""
    # 初始化一个空列表来存储分割后的子字符串
    sub_strings = []
    temp_str = ""
    # 用一个循环来遍历输入的字符串，每遇到一个花色符号，就把前面的子字符串添加到列表中
    for i in range(len(input)):
        # 取出当前的字符
        char = input[i]
        # 如果字符是花色符号，就把前面的子字符串添加到列表中，并清空子字符串
        if char in "mpsz":
            sub_strings.append(temp_str + char)
            temp_str = ''
        else:
            temp_str += char        
    # 对每个子字符串进行处理
    for sub_string in sub_strings:
        # 取出子字符串的最后一个字符，作为花色符号
        suit = sub_string[-1]
        # 取出子字符串的除了最后一个字符的部分，作为数字部分
        numbers = sub_string[:-1]
        # 用一个循环来遍历数字部分，把连续的数字拆分成单个数字，并在每个数字后面加上花色符号
        for number in numbers:
            # 把数字和花色符号拼接起来，添加到输出字符串中
            output += number + suit
    # 返回输出字符串
    return output

# 定义一个函数来把输入的字符串分割成不同的牌型，并按照万、筒、条、风牌的顺序排序
def split_and_sort(input):

    input = restore(input)
    # 初始化一个空列表来存储分割后的牌型
    result = []
    # 用一个循环来遍历输入的字符串，每两个字符为一组
    for i in range(0, len(input), 2):
        # 取出当前的两个字符，作为牌型的代号
        code = input[i:i+2]
        # 如果代号在字典中存在，就把它添加到结果列表中
        if code in tiles:
            result.append(code)
        # 否则，就忽略它
        else:
            continue
    # 对结果列表按照牌型的顺序进行排序，万在前，筒在后，条在中，风牌在最后
    result.sort(key=lambda x: (x[1], x[0]))
    # 返回结果列表
    return result

# 定义一个函数来把排序后的牌型转换成麻将牌文字符号，并输出
def convert_and_print(sorted_list):
    # 初始化一个空字符串来存储转换后的字符
    output = ""
    # 用一个循环来遍历排序后的牌型列表
    for code in sorted_list:
        # 根据字典中的对应关系，把代号转换成Unicode码点
        code_point = tiles[code]
        # 用chr()函数来把码点转换成字符
        tile = chr(code_point)
        # 把字符添加到输出字符串中
        output += tile
    # 用print()函数来输出字符串
    print(output)
# 定义一个函数，将输入简写还原

def convert_majong_unicode(origin):
    sorted_list = split_and_sort(origin)
    output = ""
    # 用一个循环来遍历排序后的牌型列表
    for code in sorted_list:
        # 根据字典中的对应关系，把代号转换成Unicode码点
        code_point = tiles[code]
        # 用chr()函数来把码点转换成字符
        tile = chr(code_point)
        # 把字符添加到输出字符串中
        output += tile
    return output


# # 测试代码
# # 输入样例
# input = "2456p888m789s333456z"
# # 调用分割和排序的函数，得到排序后的牌型列表
# sorted_list = split_and_sort(input)
# # 调用转换和输出的函数，得到麻将牌文字符号
# convert_and_print(sorted_list)
