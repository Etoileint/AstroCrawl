from __future__ import annotations


def get_chromium_flags() -> list[str]:
    """返回当前设备推荐的最佳 Chromium 渲染 flag 列表。

    仅在检测到软件渲染器时注入替代后端（如 SwiftShader），
    正常 GPU 设备返回空列表，不做任何干预。
    """
    flags: list[str] = []
    if _is_llvmpipe():
        flags.append("--use-gl=swiftshader")
    return flags


def _is_llvmpipe() -> bool:
    try:
        import subprocess

        r = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=2)
        return "llvmpipe" in r.stdout
    except Exception:
        return False
