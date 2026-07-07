# 十一、凭证热更新(中途改 key 不重启)

> 本章讲的是后端 Adapter / Provider / API 客户端在"用户在 Web 控制台
> 改了 key 之后不重启进程也能立刻生效"这件事上的统一约定。背景故事、
> 复现步骤、典型症状见 [`09-backends-and-config.md` §9.4](./09-backends-and-config.md#94-凭证热更新redline中途改-key-不要重启);
> 本章聚焦**新加一个后端 / Provider 时怎么写**才符合红线。

## 11.1 为什么这件事容易踩坑

`SERVICE_CONFIG`(`StringConfig` 实例)本身**没有**主动 push 机制:
- 配置写入通过 `set_config()` 同步落盘 + 改 `self.config[key].data`,
  但它不会通知任何订阅者。
- 因此任何"启动时把配置值拷贝到实例属性上"的写法,都会让那个实例
  属性**永远停在启动那一刻的值**。

最常见的崩法:用户没配 key → 启动 → 服务进程起来后单例 `api_key=""` →
用户配了 key → 下一次请求 `_headers()` 拼出 `"Bearer "` →
httpx 抛 `LocalProtocolError: Illegal header value b'Bearer '` →
traceback 全糊到聊天窗口。

## 11.2 三种合规写法(挑一种用)

### A. `@property` 直读(最简单)

适合:**后端就一个、没有什么状态派生自凭证**。
代表:`mimo/api.py` / `minimax/api.py` / `rh_app/api.py`。

```python
class MyAPI:
    def __init__(self) -> None:
        # 不读配置 —— 全交给 property
        self.base_url = "https://example.com"

    @property
    def api_key(self) -> str:
        """动态读取;模块导入时哪怕 key 为空也不会被冻在这里。"""
        return SERVICE_CONFIG.get_config("My_apikey").data or ""

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:  # ⚠️ 空 key 不要拼 Bearer
            h["Authorization"] = f"Bearer {self.api_key}"
        return h
```

`is_runninghub` / `server_address` / `url` 这类**从凭证派生**的字段
也必须是 property,否则修了 `base_url` 之后 `url` 不会跟着重算。

### B. 懒加载 + `refresh_config()`

适合:**配置读取有 fallback 链 / 需要按需重新算派生 URL**。
代表:`gpt_image2/api.py`。

```python
class MyAPI:
    def __init__(self) -> None:
        self._api_key: Optional[str] = None  # sentinel: None = 未读
        self._base_url: Optional[str] = None

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = SERVICE_CONFIG.get_config("My_apikey").data or ""
        return self._api_key

    def refresh_config(self) -> None:
        """清空 sentinel,下次访问时重新读 + 重新拼派生字段。"""
        self._api_key = None
        self._base_url = None
        self._recompute_urls()  # chat_url / images_url 之类
```

调用方(通常是 executor / adapter)必须**在每次请求入口**调一次:

```python
async def execute(self, request, node, *, on_progress=None):
    self.api.refresh_config()  # ← 这一行不能忘
    if not self.api.api_key:
        raise RuntimeError("My API Key 未配置")
    ...
```

### C. 显式 `update_credentials()`

适合:**provider / 客户端内部有 httpx client / 连接池 / WebSocket 等
重资源,不能每请求重建**。
代表:`seedance/api.py` / `seedance/provider.py` + `seedance/executor.py`。

```python
class MyProvider:
    def __init__(self, api_key: str = "", base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    def update_credentials(
        self,
        api_key: str,
        base_url: Optional[str] = None,
    ) -> None:
        """热更新凭证;调用方负责判断"凭证变了才调",避免无谓重建 client。"""
        self.api_key = api_key
        if base_url:
            self.base_url = base_url.rstrip("/")
        # 让下一次 _get_client() 重建连接,避免新 base_url 拿旧 client
        self._client = None
```

持有 provider 缓存的 executor 必须**每次入口比对新旧凭证**,变了就调:

```python
def _get_or_create_provider(self, name: str) -> Optional[MyProvider]:
    reg = get_registration(name)
    creds = reg.credentials()  # 每次重新解析

    cached = self._provider_cache.get(name)
    if cached is not None:
        # ⚠️ 关键:先抓 old 值,update_credentials 之后再读 self.api_key 就是新值了,
        # 日志会错。
        old_key, old_url = cached.api_key, cached.base_url
        if old_key != creds.api_key or old_url != creds.base_url:
            cached.update_credentials(api_key=creds.api_key, base_url=creds.base_url)
        return cached

    provider = MyProvider(api_key=creds.api_key, base_url=creds.base_url)
    self._provider_cache[name] = provider
    return provider
```

## 11.3 几条硬要求(无论选 A/B/C 哪种)

1. **`_headers()` / `_auth_headers()` 必须空 key 不拼 Bearer**。
   空 key 拼出 `"Bearer "`(尾空格)→ httpx `LocalProtocolError`,
   这是 2026-07 真实线上 case。

2. **业务入口必须有"前置守卫"**(`_require_api_key()` / `check_available()`)——
   key 为空时抛 `RuntimeError("...未配置 XXX...")`,而不是让请求带着
   半残头部飞出去。

3. **派生字段也算"凭证相关"**。例如 RunningHub 代理模式下
   `url = f"https://.../proxy/{api_key}"`,`api_key` 一变 `url` 必须跟着
   重算。ComfyUIAPI 把 `is_runninghub` / `server_address` / `url`
   全做成 property,就是为了应对这一点。

4. **第三方 provider 走 config_resolver 时同样适用**。`SeedanceProvider`
   注册表里的 `config_resolver` 在 `credentials()` 时**被同步调用**,
   所以你的 resolver 直接读 `SERVICE_CONFIG.get_config(...)` 就够了,
   不用做任何缓存。

## 11.4 自检清单(给 review 用)

加新后端 / Provider 时,回答这几个问题:

- [ ] `__init__` 里有没有 `self.xxx_key = SERVICE_CONFIG.get_config(...).data`?
      有 → 改成 `@property` 或 sentinel + `refresh_config`。
- [ ] `headers["Authorization"] = f"Bearer {self.api_key}"` 周围有没有
      `if self.api_key:` 防御?没有 → 加上。
- [ ] 有没有派生字段(URL / server_address / endpoint)是从 api_key / base_url
      算出来的?有 → 也做成 property。
- [ ] 有缓存的 provider / httpx client?有 → 提供 `update_credentials()`
      + 让缓存方在每次入口比对凭证。
- [ ] executor / adapter 在每次请求入口有没有刷一下凭证?
      (`refresh_config()` 或 `_get_or_create_provider(...)`)

任一项答"没有",就回到了 2026-07 那个 `Bearer ` 的 bug 路径。