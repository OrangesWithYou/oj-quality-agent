from __future__ import annotations

"""MathMode：数学客观题测验流程。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from pipeline_core import setup_logger

from ..constants import DEFAULT_MATH_QUIZ_RUN_NAME_PREFIX
from ..math_quiz import MathQuizRunner, MathQuizRuntimeConfig, parse_math_ids, parse_question_types
from .base import BaseMode


class MathMode(BaseMode):
    """数学客观题测验模式。"""

    name = "math"

    def run(self) -> Dict[str, Any]:
        args = self.args

        if not args.ppio_api_key:
            raise RuntimeError("未提供 PPIO API Key，请使用 --ppio-api-key 或设置环境变量 PPIO_API_KEY")

        question_types = parse_question_types(args.math_question_types)

        # 解析 --math-ids（空字符串 -> None，表示不启用 ID 抽样）
        raw_ids = getattr(args, "math_ids", "") or ""
        ids = parse_math_ids(raw_ids) if raw_ids.strip() else None

        runtime = MathQuizRuntimeConfig(
            ppio_api_key=args.ppio_api_key,
            ppio_base_url=args.ppio_base_url,
            ppio_model=args.model,
            question_types=question_types,
            max_cases=max(0, int(args.math_max_cases)),
            samples=max(1, int(getattr(args, "math_samples", 4))),
            request_interval=float(getattr(args, "math_request_interval", 0.5)),
            temperature=float(args.math_temperature),
            top_p=float(args.math_top_p),
            max_tokens=int(args.math_max_tokens),
        )

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name_prefix = getattr(args, "math_quiz_run_name_prefix", DEFAULT_MATH_QUIZ_RUN_NAME_PREFIX)
        run_dir = Path(args.math_quiz_output_dir) / f"{run_name_prefix}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        logger = setup_logger(run_dir / "run.log", logger_name="ppio_math_quiz")

        return MathQuizRunner(runtime, logger).run(
            cases_file=Path(args.math_cases_file),
            run_dir=run_dir,
            ids=ids,
        )
