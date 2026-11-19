"""创建 pywebview 原生窗口（外观是独立软件，界面是本地 HTML）。"""
from __future__ import annotations

import webview

from . import config, db, embedded_player, single_instance, theme_native
from .api import Api


def startup_url(settings: dict | None = None) -> str:
    """首屏 URL：把持久化的主题作为参数带上，让 CSS 第一次解析时就是正确主题。

    主题存在 SQLite 而非 localStorage（private_mode 不持久），JS 侧要等
    pywebviewready 后异步 IPC 才能读到；若首屏写死 light，暗色用户会先看到白表头、
    切一下主题才变黑。这里在 Python 侧同步注入，配合 index.html head 里的同步脚本。
    """
    raw = str((settings or {}).get("theme", config.DEFAULTS["theme"]) or "light").strip().lower()
    theme = raw if raw in ("light", "dark") else "light"
    # 必须传本地路径（不是 file:// URI）：pywebview 6.2.1 只对本地路径启动内置 HTTP
    # server，把 query 交给 HTTP 层解析；file:// URI 会被原样交给 WebView2，路径含空格
    # (%20) 且带 ?theme= 时被当成文件名的一部分 → ERR_FILE_NOT_FOUND（v0.1.8 回归）。
    return str(config.WEB_DIR / "index.html") + f"?theme={theme}"


def sync_native_theme_before_show(settings: dict | None = None) -> None:
    """窗口显示前把原生标题栏刷成主题色，避免第一帧白条。

    Windows 标题栏由 DWM 绘制，CSS 管不到；之前只靠页面 JS 加载完的 IPC 去同步，
    暗色用户会先看到一条白标题栏再变黑。``before_show`` 时 Form 的 HWND 已创建、
    还没有 Show，所以这里允许「未显示窗口」并只在显示前同步一次。
    DWM 不可用、找不到句柄等一律静默降级，绝不阻断窗口显示。
    """
    try:
        raw = str((settings or {}).get("theme", config.DEFAULTS["theme"]) or "light").strip().lower()
        theme = raw if raw in ("light", "dark") else "light"
        # pywebview 的 BrowserForm 按系统「应用模式」写标题栏，并在系统偏好变化时
        # 反复重设；先接管它的判定，否则浅色系统 + 深色软件会被它刷成白条。
        theme_native.hook_webview_title_bar()
        theme_native.apply(theme, require_visible=False, retries=2)
    except Exception:
        pass


def create_window(debug: bool = False) -> None:
    # 任务栏/快捷方式重复点击时，只把已有窗口置前并退出，绝不再开第二个窗口。
    # 第二个进程一旦也去初始化 WebView2，会与已有实例抢资源并显著拖慢启动。
    if not single_instance.guard():
        return

    # Api() 内部已完成 init_db() + 内置词典装载，这里不再重复执行（启动提速）。
    api = Api()
    settings = db.all_settings()
    # 先置位软件主题：pywebview 建窗时会立刻按它判定标题栏深浅，避免第一帧走系统主题。
    theme_native.set_theme_state(settings.get("theme"))

    window = webview.create_window(
        "Magic Video",
        startup_url(settings),
        js_api=api,
        width=int(settings.get("window_width", 1180)),
        height=int(settings.get("window_height", 780)),
        min_size=(960, 620),
        # 不传 background_color：EdgeChromium 后端会在 CoreWebView2 尚未就绪时
        # 访问 DefaultBackgroundColor，在 WebView2 157 上会抛 E_NOINTERFACE。
        # 背景色完全由 style.css 控制，界面观感不变。
        text_select=True,
    )
    api.attach_window(window)
    # 窗口第一次绘制前就把原生标题栏定成主题色（before_show 是同步事件，
    # 在 Form.Show() 之前执行）。页面里的 sync_native_theme 仍保留，作为二次兜底。
    window.events.before_show += lambda: sync_native_theme_before_show(settings)
    # 窗口真正显示后再同步一次：pywebview 在 Shown 前后也可能触发系统偏好回调，
    # 这一发是廉价保险（只写 4 个 DWM 属性，不建线程、不设定时器）。
    window.events.shown += lambda: sync_native_theme_before_show(settings)
    # 后台记录默认自动启动（设置页可关）。放在窗口创建后、WebView 启动前，
    # 即使前端脚本加载失败也能生效；失败只降级，绝不阻断窗口。
    try:
        api.ensure_daemon_auto_start()
    except Exception:
        pass
    # private_mode=True：每次启动用临时 WebView2 profile，避免升级安装后仍加载缓存里的旧 app.js。
    # 所有状态都在 SQLite，不需要浏览器端持久化；配合 index.html 的 app.js?v=<版本> 双保险。
    try:
        webview.start(gui="edgechromium", debug=debug, private_mode=True)
    finally:
        # 主窗口关闭时把 PotPlayer 还原成独立窗口，绝不留下孤儿 WS_CHILD 窗口。
        embedded_player.cleanup()
