from __future__ import annotations

from typing import Dict

# =========================
# 通用默认参数（general）
# =========================
# 默认题库路径：当用户不指定 --cases-file 时使用。
DEFAULT_CASES_FILE = "cases_to_test.json"

# 默认运行输出目录：当用户不指定 --output-dir 时使用。
DEFAULT_OUTPUT_DIR = "runs"

# 默认运行目录前缀：最终目录形如 runs/run_cases_YYYYMMDD_HHMMSS。
DEFAULT_RUN_NAME_PREFIX = "run_cases"

# =========================
# 数学客观题测验默认参数
# =========================
# 数学客观题默认输入文件（支持 list 或 {"cases": [...]}）。
DEFAULT_MATH_CASES_FILE = "cases_math.json"

# 数学客观题默认输出目录。
DEFAULT_MATH_QUIZ_OUTPUT_DIR = "runs/math_quiz"

# 数学客观题默认运行目录前缀。
DEFAULT_MATH_QUIZ_RUN_NAME_PREFIX = "run_math_quiz"

# 数学客观题默认题型过滤（判断 -> 单选 -> 多选）。
DEFAULT_MATH_QUESTION_TYPES = "判断,单选,多选"

# =========================
# 业务域预设（code / math）
# =========================
# 用于 --domain 模式下的自动路径隔离。
DOMAIN_PRESETS: Dict[str, Dict[str, str]] = {
    "code": {
        # code 域默认题库路径。
        "cases_file": "cases/code/cases_to_test.json",
        # code 域默认输出目录。
        "output_dir": "runs/code",
        # code 域默认运行目录前缀。
        "run_name_prefix": "run_code_cases",
    },
    "math": {
        # math 域默认题库路径。
        "cases_file": "cases/math/cases_to_test.json",
        # math 域默认输出目录。
        "output_dir": "runs/math",
        # math 域默认运行目录前缀。
        "run_name_prefix": "run_math_cases",
    },
}
