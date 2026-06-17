from __future__ import annotations

"""外部服务封装：JudgeClient（Judge0 判题接口）。"""

import logging
import time
from typing import Any, Dict, Optional

import requests

from .core import SESSION, safe_int


class JudgeClient:
    """Judge0 接口隔离层。"""

    def __init__(
        self,
        base_url: str,
        language_id: int,
        poll_interval_sec: float,
        poll_timeout_sec: int,
        *,
        session: requests.Session = SESSION,
        logger: Optional[logging.Logger] = None,
    ):
        self.base_url = base_url
        self.language_id = language_id
        self.poll_interval_sec = poll_interval_sec
        self.poll_timeout_sec = poll_timeout_sec
        self.session = session
        self.logger = logger

    def submit_code(self, source_code: str, stdin: str = "", expected_output: Optional[str] = None) -> str:
        """提交代码并返回 Judge token。"""
        payload: Dict[str, Any] = {
            "source_code": source_code,
            "language_id": self.language_id,
            "stdin": stdin,
        }
        if expected_output is not None:
            payload["expected_output"] = expected_output

        resp = self.session.post(
            f"{self.base_url}/submissions?base64_encoded=false&wait=false",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def get_result(self, token: str) -> Dict[str, Any]:
        """按 token 拉取当前判题状态。"""
        resp = self.session.get(
            f"{self.base_url}/submissions/{token}?base64_encoded=false",
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def poll_result(self, token: str) -> Dict[str, Any]:
        """轮询判题结果，直到结束或超时。"""
        start = time.time()
        while True:
            result = self.get_result(token)
            status = result.get("status") or {}
            status_id = safe_int(status.get("id"), -1)

            if status_id not in (1, 2):
                result.setdefault("token", token)
                return result

            if time.time() - start > self.poll_timeout_sec:
                return {
                    "token": token,
                    "status": {"id": -1, "description": "Timeout"},
                    "stdout": None,
                    "stderr": None,
                    "compile_output": None,
                }

            time.sleep(self.poll_interval_sec)

    def judge(self, source_code: str, stdin: str = "", expected_output: Optional[str] = None) -> Dict[str, Any]:
        """提交并轮询，返回最终判题结果。"""
        token = self.submit_code(source_code=source_code, stdin=stdin, expected_output=expected_output)
        result = self.poll_result(token)
        result.setdefault("token", token)
        return result
