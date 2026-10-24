"""百度翻译开放平台客户端。

一个程序里支持三种服务（在「设置 → 翻译服务」里切换）：

1. 大模型文本翻译（推荐，译文更自然）
   POST https://fanyi-api.baidu.com/ait/api/aiTextTranslate
   JSON 体 {appid, q, from, to}，请求头 Authorization: Bearer <密钥>

2. 通用文本翻译（经典接口，支持 auto 识别语种）
   POST https://fanyi-api.baidu.com/api/trans/vip/translate
   sign = md5(appid + q + salt + 密钥)

3. 领域文本翻译（11 个垂直领域，仅支持中英互译）
   POST https://fanyi-api.baidu.com/api/trans/vip/fieldtranslate
   sign = md5(appid + q + salt + domain + 密钥)，domain 取值见 DOMAINS

只用标准库（urllib），不依赖第三方包，方便拷给别人直接用。
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

GENERAL_URL = "https://fanyi-api.baidu.com/api/trans/vip/translate"
DOMAIN_URL = "https://fanyi-api.baidu.com/api/trans/vip/fieldtranslate"
LLM_URL = "https://fanyi-api.baidu.com/ait/api/aiTextTranslate"
USER_AGENT = "BaiduQuickTranslate/1.1"

# 单次请求 q 的官方上限是 6000 字节，这里留一点余量并支持自动分段
MAX_CHUNK_BYTES = 4800

# ---------------------------------------------------------------- 翻译服务
SERVICES: list[tuple[str, str]] = [
    ("llm", "大模型文本翻译"),
    ("general", "通用文本翻译"),
    ("domain", "领域文本翻译"),
]
SERVICE_NAME: dict[str, str] = dict(SERVICES)
DEFAULT_SERVICE = "general"

# 领域文本翻译支持的领域代码（百度官方文档）
DOMAINS: list[tuple[str, str]] = [
    ("academic", "学术论文"),
    ("senimed", "生物医药"),
    ("it", "信息技术"),
    ("finance", "金融财经"),
    ("machinery", "机械制造"),
    ("novel", "网络文学（仅中→英）"),
    ("news", "新闻资讯"),
    ("wiki", "人文社科（仅中→英）"),
    ("aerospace", "航空航天"),
    ("law", "法律法规"),
    ("contract", "合同"),
]
DOMAIN_NAME: dict[str, str] = dict(DOMAINS)
DEFAULT_DOMAIN = "academic"

# 常用语种（百度开放平台代码）
LANGUAGES: list[tuple[str, str]] = [
    ("auto", "自动检测"),
    ("zh", "中文"),
    ("cht", "繁体中文"),
    ("en", "英语"),
    ("jp", "日语"),
    ("kor", "韩语"),
    ("fra", "法语"),
    ("de", "德语"),
    ("spa", "西班牙语"),
    ("ru", "俄语"),
    ("pt", "葡萄牙语"),
    ("it", "意大利语"),
    ("ara", "阿拉伯语"),
    ("th", "泰语"),
    ("vie", "越南语"),
]
LANG_NAME: dict[str, str] = dict(LANGUAGES)
# 用户/系统可能给我们的常见别名
LANG_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-tw": "cht",
    "zh-hant": "cht",
    "zh-hk": "cht",
    "jp": "jp",
    "ja": "jp",
    "ko": "kor",
    "kr": "kor",
    "fr": "fra",
    "es": "spa",
    "ar": "ara",
    "vi": "vie",
    "auto": "auto",
}

# 通用错误码 → 中文提示；部分错误码在大模型/领域服务下含义略有不同
ERROR_MESSAGES: dict[str, str] = {
    "52001": "请求超时，请稍后重试",
    "52002": "百度翻译系统错误，请稍后重试",
    "52003": "APPID 未授权：请确认 APPID 填写正确，且已在控制台开通对应服务",
    "54000": "必填参数为空",
    "54001": "签名错误：请检查「密钥」是否填写正确、与 APPID 是否配套",
    "54003": "访问频率受限：请求太频繁了，请稍后再试",
    "54004": "账户余额不足：免费额度已用完，请到控制台查看用量",
    "54005": "长文本请求太频繁，请稍后重试",
    "58000": "客户端 IP 非法：请在控制台检查 IP 白名单设置",
    "58001": "译文语言方向不支持",
    "58002": "服务当前已关闭，请到控制台开启",
    "90107": "认证未通过或未生效，请到控制台完成认证",
}

# 不同服务对同一错误码的补充说明
_SERVICE_ERROR_HINTS: dict[str, dict[str, str]] = {
    "52003": {
        "llm": "APPID 未授权：请确认 APPID/密钥正确，且已开通「大模型文本翻译」服务",
        "general": "APPID 未授权：请确认 APPID/密钥正确，且已开通「通用文本翻译」服务",
        "domain": "APPID 未授权：请确认 APPID/密钥正确，且已开通「领域文本翻译」服务",
    },
    "54001": {
        "llm": "密钥无效或「大模型文本翻译」未开通：该服务需在控制台开通并通过认证，"
               "且「密钥」要与 APPID 配套（不是 API Key 管理里的 Key）",
        "general": "签名错误：请检查「密钥」是否填写正确、与 APPID 是否配套",
        "domain": "签名错误：领域翻译的签名包含领域代码，请检查密钥与领域选择是否配套",
    },
    "58002": {
        "llm": "「大模型文本翻译」服务已关闭，请到百度翻译控制台开启",
        "general": "「通用文本翻译」服务已关闭，请到百度翻译控制台开启",
        "domain": "「领域文本翻译」服务已关闭，请到百度翻译控制台开启",
    },
    "58001": {
        "llm": "译文语言方向不支持",
        "general": "译文语言方向不支持",
        "domain": "语言方向不支持：领域翻译只支持中文 ↔ 英语（部分领域仅支持中→英）",
    },
}


class BaiduTranslateError(Exception):
    """调用百度翻译接口失败。code 为百度错误码或本地错误标识。"""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = message
        super().__init__(f"{message}（错误码 {code}）")


@dataclass
class Translation:
    """一次翻译的结果。"""

    source: str
    target: str
    from_lang: str = "auto"
    to_lang: str = "zh"
    service: str = DEFAULT_SERVICE
    domain: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def from_name(self) -> str:
        return LANG_NAME.get(self.from_lang, self.from_lang)

    @property
    def to_name(self) -> str:
        return LANG_NAME.get(self.to_lang, self.to_lang)

    @property
    def lang_label(self) -> str:
        return f"{self.from_name} → {self.to_name}"

    @property
    def service_name(self) -> str:
        return SERVICE_NAME.get(self.service, self.service)

    @property
    def service_short(self) -> str:
        return {"llm": "大模型", "general": "通用", "domain": "领域"}.get(self.service, "")

    @property
    def domain_name(self) -> str:
        return DOMAIN_NAME.get(self.domain, self.domain)


def make_sign(appid: str, query: str, salt: str, secret: str) -> str:
    """通用文本翻译签名：md5(appid + q + salt + 密钥)。"""
    raw = f"{appid}{query}{salt}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def make_domain_sign(appid: str, query: str, salt: str, domain: str, secret: str) -> str:
    """领域文本翻译签名：md5(appid + q + salt + domain + 密钥)。"""
    raw = f"{appid}{query}{salt}{domain}{secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def normalize_service(code: str) -> str:
    code = (code or "").strip().lower()
    return code if code in SERVICE_NAME else DEFAULT_SERVICE


def normalize_domain(code: str) -> str:
    code = (code or "").strip().lower()
    return code if code in DOMAIN_NAME else DEFAULT_DOMAIN


def normalize_lang(code: str) -> str:
    code = (code or "").strip()
    return LANG_ALIASES.get(code.lower(), code)


def _looks_chinese(text: str) -> bool:
    """粗略判断是否含中文（含常用标点与部首区）。"""
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:      # CJK 统一表意文字
            return True
        if 0x3400 <= code <= 0x4DBF:      # 扩展 A
            return True
        if 0x3000 <= code <= 0x303F:      # 中文标点
            return True
        if 0xF900 <= code <= 0xFAFF:      # 兼容表意文字
            return True
    return False


def detect_direction(text: str, target_mode: str = "auto") -> tuple[str, str]:
    """根据文本内容和目标语言设置，推断 (from_lang, to_lang)。"""
    mode = normalize_lang(target_mode)
    if mode and mode != "auto":
        # 用户指定了目标语种：源语种自动识别；
        # 如果文本本来就是目标语种，则自动反向翻译回中/英，避免「中文→中文」。
        if mode in ("zh", "cht") and _looks_chinese(text):
            return "zh", "en"
        if mode == "en" and not _looks_chinese(text):
            return "en", "zh"
        return "auto", mode
    # 自动模式：中 → 英，其它 → 中
    if _looks_chinese(text):
        return "zh", "en"
    return "auto", "zh"


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_chunks(text: str, limit_bytes: int = MAX_CHUNK_BYTES) -> list[str]:
    """把长文本切成不超过 limit_bytes 的若干段。

    优先在换行处切，其次在句号等标点处切，最后才硬切；
    各段按顺序拼接后与原文本完全一致（不会丢字、加字）。
    """
    if _byte_len(text) <= limit_bytes:
        return [text]

    # 1) 先切成「不超过上限的最小单元」：整行 → 整句 → 单字
    units: list[str] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _byte_len(line) <= limit_bytes:
            units.append(line)
            if index < len(lines) - 1:
                units.append("\n")
            continue
        piece = ""
        for ch in line:
            if _byte_len(piece + ch) > limit_bytes:
                units.append(piece)
                piece = ""
            piece += ch
            if ch in "。！？!?；;…":
                units.append(piece)
                piece = ""
        if piece:
            units.append(piece)
        if index < len(lines) - 1:
            units.append("\n")

    # 2) 把最小单元拼回不超过上限的块
    chunks: list[str] = []
    buffer = ""
    for unit in units:
        if _byte_len(buffer + unit) <= limit_bytes:
            buffer += unit
            continue
        if buffer:
            chunks.append(buffer)
        buffer = unit
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c] or [text]


class BaiduTranslator:
    """封装百度翻译的三种文本翻译服务。"""

    def __init__(
        self,
        appid: str,
        secret: str,
        timeout: float = 10.0,
        service: str = DEFAULT_SERVICE,
        domain: str = DEFAULT_DOMAIN,
    ):
        self.appid = (appid or "").strip()
        self.secret = (secret or "").strip()
        self.timeout = max(3.0, float(timeout))
        self.service = normalize_service(service)
        self.domain = normalize_domain(domain)
        if not self.appid or not self.secret:
            raise BaiduTranslateError("no-credential", "还没有填写 APPID / 密钥，请先在「设置」里完成配置")

    # ------------------------------------------------------------------ 请求构造
    def _build_request(
        self, query: str, from_lang: str, to_lang: str
    ) -> tuple[str, bytes, dict[str, str]]:
        """构造一次 HTTP 请求（url, body, headers），独立出来方便自检。"""
        if self.service == "llm":
            payload = {
                "appid": self.appid,
                "q": query,
                "from": from_lang,
                "to": to_lang,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Bearer {self.secret}",
                "User-Agent": USER_AGENT,
            }
            return LLM_URL, data, headers

        salt = str(random.randint(10000, 99999999))
        if self.service == "domain":
            payload = {
                "q": query,
                "from": from_lang,
                "to": to_lang,
                "appid": self.appid,
                "salt": salt,
                "domain": self.domain,
                "sign": make_domain_sign(self.appid, query, salt, self.domain, self.secret),
            }
            url = DOMAIN_URL
        else:
            payload = {
                "q": query,
                "from": from_lang,
                "to": to_lang,
                "appid": self.appid,
                "salt": salt,
                "sign": make_sign(self.appid, query, salt, self.secret),
            }
            url = GENERAL_URL
        data = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": USER_AGENT,
        }
        return url, data, headers

    # ------------------------------------------------------------------ 底层请求
    def _error_message(self, code: str, raw_message: str) -> str:
        hints = _SERVICE_ERROR_HINTS.get(code)
        if hints and self.service in hints:
            return hints[self.service]
        return ERROR_MESSAGES.get(code) or raw_message or "接口返回错误"

    def _request_once(self, query: str, from_lang: str, to_lang: str) -> dict:
        url, data, headers = self._build_request(query, from_lang, to_lang)
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise BaiduTranslateError(f"http-{exc.code}", f"百度服务器返回 HTTP {exc.code}") from exc
        except socket.timeout as exc:
            raise BaiduTranslateError("timeout", "请求超时，请检查网络或稍后重试") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise BaiduTranslateError("network", f"网络连接失败：{reason}") from exc
        except OSError as exc:
            raise BaiduTranslateError("network", f"网络连接失败：{exc}") from exc

        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BaiduTranslateError("bad-response", "接口返回的内容无法解析，请稍后重试") from exc

        if not isinstance(result, dict):
            raise BaiduTranslateError("bad-response", "接口返回格式异常")
        code = result.get("error_code")
        if code not in (None, "", "0", 0):
            code = str(code)
            raise BaiduTranslateError(code, self._error_message(code, str(result.get("error_msg") or "")))
        return result

    # ------------------------------------------------------------------ 对外接口
    def _prepare_languages(self, from_lang: str, to_lang: str) -> tuple[str, str]:
        """按服务限制处理语种：领域翻译只支持中英互译。"""
        if self.service == "domain":
            if to_lang not in ("zh", "en") or from_lang not in ("auto", "zh", "en"):
                raise BaiduTranslateError(
                    "lang-unsupported",
                    "领域文本翻译只支持中英互译，请把目标语言设为「自动（中↔英）」或英语/中文",
                )
            if from_lang == "auto":
                from_lang = "en" if to_lang == "zh" else "zh"
        return from_lang, to_lang

    def translate(self, text: str, from_lang: str = "auto", to_lang: str = "zh") -> Translation:
        text = (text or "").strip()
        if not text:
            raise BaiduTranslateError("empty", "没有可翻译的文本")

        from_lang = normalize_lang(from_lang) or "auto"
        to_lang = normalize_lang(to_lang) or "zh"
        from_lang, to_lang = self._prepare_languages(from_lang, to_lang)

        pieces = _split_chunks(text)
        translated: list[str] = []
        first: dict = {}
        real_from, real_to = from_lang, to_lang
        for piece in pieces:
            result = self._request_once(piece, from_lang, to_lang)
            if not first:
                first = result
                real_from = result.get("from", from_lang) or from_lang
                real_to = result.get("to", to_lang) or to_lang
            items = result.get("trans_result") or []
            if not items:
                raise BaiduTranslateError("empty-result", "接口没有返回译文")
            translated.append("\n".join(str(item.get("dst", "")) for item in items))

        return Translation(
            source=text,
            target="\n".join(translated),
            from_lang=real_from,
            to_lang=real_to,
            service=self.service,
            domain=self.domain if self.service == "domain" else "",
            raw=first,
        )

    def test(self) -> Translation:
        """用一句话做连通性 / 密钥校验。"""
        return self.translate("hello", from_lang="en", to_lang="zh")
