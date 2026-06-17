# Judge0 连通性最小化验证脚本：
# - 提交一段固定代码
# - 轮询判题结果
# - 打印最终返回
import requests
import time

# Judge0 公共服务地址（演示/排障用）。
BASE_URL = "https://ce.judge0.com"

# 使用 Session 复用连接，并忽略系统代理避免网络污染。
session = requests.Session()
session.trust_env = False

# 统一请求头。
headers = {"Content-Type": "application/json"}


def submit_code(source_code, stdin="", expected_output=None):
    """提交代码到 Judge0，返回 submission token。"""

    # 构造最小提交载荷。
    payload = {
        "source_code": source_code,
        "language_id": 71,
        "stdin": stdin,
    }
    # 若提供 expected_output，则由 Judge0 直接做输出比对。
    if expected_output is not None:
        payload["expected_output"] = expected_output

    # wait=false：异步提交，仅拿 token。
    r = session.post(
        f"{BASE_URL}/submissions?base64_encoded=false&wait=false",
        json=payload,
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["token"]


def get_result(token):
    """根据 token 查询判题结果。"""

    r = session.get(
        f"{BASE_URL}/submissions/{token}?base64_encoded=false",
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# 准备一段最小示例代码：读取两行整数并输出和。
code = "a=int(input())\nb=int(input())\nprint(a+b)"

# 1) 提交代码，拿到 token。
token = submit_code(code, "1\n2\n", "3\n")
print("token:", token)

# 2) 轮询直到状态不再是 In Queue / Processing。
while True:
    result = get_result(token)
    if result["status"]["id"] not in (1, 2):
        # 3) 打印最终判题结果。
        print(result)
        break
    time.sleep(1)