"""淘宝发布器：双通道。

通道 A（open_api）：taobao.item.add.v2 —— 需要 app_key/app_secret/session_key 与类目授权
通道 B（backend）：  商家后台发布接口重放 —— 用自己店铺登录态，无需资质

图片处理：
  开放平台 → taobao.picture.upload（Base64 二进制）拿 picture_path 拼 URL
  后台通道 → 商家图片空间上传接口
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ..core.config import get_settings
from ..core.models import Item, PublishResult
from .base import BasePublisher

GATEWAY = "https://eco.taobao.com/router/rest"


class TaobaoPublisher(BasePublisher):
    target = "taobao"

    def __init__(self, shop: str = "default", mode: str = "draft",
                 channel: str = "open_api"):
        super().__init__(shop=shop, mode=mode)
        self.channel = channel
        s = get_settings()
        self.app_key = s.get("taobao.app_key", "")
        self.app_secret = s.get("taobao.app_secret", "")
        self.session_key = s.get(f"taobao.shops.{shop}.session_key", "")

    # ------------------------------------------------------------ 载荷
    def build_payload(self, item: Item, target_cid: str | None = None) -> dict:
        cid = target_cid or self.resolve_category(item)
        title = self.sanitizer.clean_title(item.title)

        # 属性映射（关键属性 + 销售属性）
        prop_pairs: list[str] = []
        prop_order: list[dict] = []
        for sp in item.sale_props:
            spec = self.prop_mapper.map_sale_prop(item.platform, self.target, sp.pid)
            if not spec:
                continue
            prop_order.append({"src_pid": sp.pid, "tgt_pid": spec["target_pid"],
                               "value_map": spec.get("value_map", {})})
            # 销售属性值也写进 props 串（淘系要求）
            for v in sp.values:
                mapped = self.prop_mapper.map_value(spec.get("value_map", {}), v.vid, v.name)
                if mapped:
                    prop_pairs.append(f"{spec['target_pid']}:{mapped['vid']}")

        skus = self.sku_builder.rebuild(item, prop_order, stock_mode="fixed")

        images = [img.hosted_url or img.local_path or img.url
                  for img in item.main_images[:5]]
        desc = self.sanitizer.clean_html(item.detail_html or "")

        return {
            "cid": cid,
            "title": title,
            "props": ";".join(dict.fromkeys(prop_pairs)),
            "desc": desc,
            "images": images,
            "price": str(min((s["price"] for s in skus), default=0)),
            "num": str(sum(s["quantity"] for s in skus) or 999),
            "stuff_status": "全新",
            "approve_status": "onsale" if self.mode == "direct" else "instock",
            "skus": skus,
            "sku_properties": ",".join(s["props"] for s in skus),
            "sku_prices": ",".join(str(s["price"]) for s in skus),
            "sku_quantities": ",".join(str(s["quantity"]) for s in skus),
            "sku_outer_ids": ",".join(str(s["outer_id"]) for s in skus),
        }

    # ------------------------------------------------------------ 提交
    def submit(self, payload: dict) -> PublishResult:
        """**M2 起禁用直连提交。**

        复审 blocker：配置了开放平台凭据时，这个方法会真的 ``httpx.post`` 提交商品。
        发布必须在离线流水线（``src/publish/pipeline.py``）里完成，
        真实提交要等发布接口验证通过后单独开放。

        所以这里无条件拒绝，且**在任何网络调用之前**拒绝。
        """
        return PublishResult(
            ok=False, target=self.target, mode=self.mode,
            error_code="CHANNEL_DISABLED",
            error_msg=(
                "TaobaoPublisher 的直连提交通道已禁用（M2 起发布统一走 "
                "PublishPipeline 的 dry_run / draft 模式）。"
                "真实提交需要先验证发布接口，不在当前范围内。"),
            raw_response={"payload_preview": {k: str(v)[:120]
                                              for k, v in payload.items()}},
        )

    # -------------------------------------------------------- 通道 A
    def _submit_open_api(self, payload: dict) -> PublishResult:
        # 复审 blocker：直连提交一律禁用，先于任何网络调用返回
        return PublishResult(
            ok=False, target=self.target, mode=self.mode,
            error_code="CHANNEL_DISABLED",
            error_msg="直连提交通道已禁用（M2 起发布统一走 PublishPipeline）",
        )

        # ---- 以下为待验证的原始实现，当前不可达 ----
        if not (self.app_key and self.app_secret and self.session_key):
            return PublishResult(
                ok=False, target=self.target, mode=self.mode,
                error_code="NO_CREDENTIALS",
                error_msg="缺少开放平台凭据（app_key/app_secret/session_key），改用 channel='backend'",
            )
        params = {
            "method": "taobao.item.add.v2",
            "app_key": self.app_key,
            "session": self.session_key,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            **{k: v for k, v in payload.items() if k != "skus"},
        }
        params["sign"] = self._sign(params)
        try:
            r = httpx.post(GATEWAY, data=params, timeout=30)
            body = r.json()
        except Exception as e:
            return PublishResult(ok=False, target=self.target, mode=self.mode,
                                 error_msg=str(e), retryable=True)
        err = body.get("error_response")
        if err:
            return PublishResult(
                ok=False, target=self.target, mode=self.mode,
                error_code=err.get("sub_code") or err.get("code"),
                error_msg=err.get("sub_msg") or err.get("msg"),
                retryable=err.get("code") in ("7", "isp.top-remote-service-unavailable"),
                raw_response=body,
            )
        resp = body.get("item_add_response", {}).get("item", {})
        return PublishResult(ok=True, target=self.target, mode=self.mode,
                             target_item_id=str(resp.get("num_iid") or ""),
                             raw_response=body)

    def _sign(self, params: dict) -> str:
        items = sorted((k, v) for k, v in params.items() if k != "sign" and v is not None)
        raw = self.app_secret + "".join(f"{k}{v}" for k, v in items) + self.app_secret
        return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    # -------------------------------------------------------- 通道 B
    def _submit_backend(self, payload: dict) -> PublishResult:
        """后台通道：重放商家后台发布接口。

        真实实现需要：
        1. 从 config/cookies/<shop>.json 加载 Playwright storage_state（含 _tb_token_ / cookie2）
        2. 先 GET 发布页初始化接口，拿 csrf 与属性元数据
        3. POST 发布载荷；遇 x5sec 响应时走 Playwright 过一次滑块后回写 Cookie
        """
        # 复审 blocker：直连提交一律禁用，先于任何网络调用返回
        return PublishResult(
            ok=False, target=self.target, mode=self.mode,
            error_code="CHANNEL_DISABLED",
            error_msg="直连提交通道已禁用（M2 起发布统一走 PublishPipeline）",
        )

        # ---- 以下为待验证的原始实现，当前不可达 ----
        s = get_settings()
        state_file = Path(s.cookie_dir) / f"{self.shop}.json"
        if not state_file.exists():
            return PublishResult(
                ok=False, target=self.target, mode=self.mode,
                error_code="NO_LOGIN_STATE",
                error_msg=f"缺少店铺登录态 {state_file}（用 Playwright 扫码登录后导出 storage_state）",
            )
        endpoint = s.get("taobao.publish_endpoint", "")
        if not endpoint:
            return PublishResult(
                ok=False, target=self.target, mode=self.mode,
                error_code="NO_ENDPOINT",
                error_msg="未配置 config: taobao.publish_endpoint（后台发布接口路径，随版本变化，需自行抓取）",
                raw_response={"payload_preview": {k: str(v)[:120] for k, v in payload.items()}},
            )
        return PublishResult(
            ok=False, target=self.target, mode=self.mode,
            error_code="NOT_IMPLEMENTED",
            error_msg="后台通道需填入实际 endpoint 与表单字段映射后启用",
            raw_response={"payload_preview": {k: str(v)[:120] for k, v in payload.items()}},
        )


# ---------------------------------------------------------------- 图片空间

def upload_picture(app_key: str, app_secret: str, session: str, file_path: str,
                   category_id: str = "0") -> str:
    """taobao.picture.upload：图片二进制 Base64 上传，返回可直接用的 alicdn URL。"""
    raw = Path(file_path).read_bytes()
    params = {
        "method": "taobao.picture.upload",
        "app_key": app_key,
        "session": session,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "sign_method": "md5",
        "picture_category_id": category_id,
        "image_input_title": Path(file_path).name,
        "img": base64.b64encode(raw).decode(),
    }
    items = sorted((k, v) for k, v in params.items() if k != "sign")
    raw_str = app_secret + "".join(f"{k}{v}" for k, v in items) + app_secret
    params["sign"] = hashlib.md5(raw_str.encode()).hexdigest().upper()
    r = httpx.post(GATEWAY, data=params, timeout=60).json()
    pic = (r.get("picture_upload_response") or {}).get("picture") or {}
    path = pic.get("picture_path") or ""
    return f"https://img.alicdn.com/imgextra/{path}" if path else ""
