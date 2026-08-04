# 图片混淆插件

后端在 `backend.py`，提供独立的 `encode(data: bytes) -> bytes` 和 `decode(data: bytes) -> bytes` 接口。算法对应小番茄网站公开描述的 Gilbert/广义 Hilbert 曲线与黄金比例偏移；输出为 JPEG（质量 95）。这属于可逆视觉混淆，不是密码学加密。

QQ 私聊支持好友和陌生人消息：

```text
混淆 <图片/引用/消息记录中的图片...>
解混淆 <图片/引用/消息记录中的图片...>
```

精确发送 `混淆` 或 `解混淆` 后，插件会等待 60 秒；收到的消息记录、引用消息和当前消息中的多张图片都会处理。混淆保存输入图，解混淆保存解码后的图，位置为 `data/image_obfuscator/source/{encode,decode}/`。

CLI：

```bash
python -m kanamibot.plugins.image_obfuscator.cli encode a.png b.jpg -o out
python -m kanamibot.plugins.image_obfuscator.cli decode --base64 'data:image/png;base64,...' -o out
```
