# -*- coding: utf-8 -*-
"""通过 GitHub REST API 把本仓库推上去。

为什么不用 `git push`：本机代理只放行 `api.github.com`，
`github.com` 的 HTTPS 出口被挡（`CONNECT tunnel failed, response 502`），
`git push/clone/ls-remote` 全部走不通。所以改成把本地提交逐条重建成
GitHub 上的 blob → tree → commit → ref，全程只打 api.github.com。

用法：
    set GH_TOKEN=ghp_xxxxxxxx          # 或用 $env:GH_TOKEN
    python tools\\push_to_github.py --repo codex-quota-tray --create --private

    # 已建好仓库、只想更新代码：
    python tools\\push_to_github.py --repo codex-quota-tray

token 从环境变量读，不写在命令行里（避免进 shell 历史）。
需要 classic PAT 的 `repo` 权限，或 fine-grained 的
Contents: Read and write（要建仓还需 Administration: Read and write）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
BRANCH = "main"


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{result.stderr.decode('utf-8', 'replace')}")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def token_from_git_credential() -> str | None:
    """向配置好的 git 凭据管理器要 github.com 的凭据（GCM 等）。

    这样 token 既不用写进命令行，也不会出现在 shell 历史或对话里。
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    try:
        result = subprocess.run(
            ["git", "credential", "fill"], capture_output=True, text=True,
            input="protocol=https\nhost=github.com\n\n", env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            value = line[len("password="):].strip()
            return value or None
    return None


class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CodexQuotaTray-push",
            **({"Content-Type": "application/json"} if data else {}),
        })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {path} 网络失败：{exc}") from exc


def parse_commit(sha: str) -> dict:
    """从 git cat-file 的原始对象里取 author / committer / message。"""
    raw = git("cat-file", "commit", sha)
    head, _, message = raw.partition("\n\n")
    info = {}
    for line in head.splitlines():
        if line.startswith("author "):
            info["author"] = line[7:]
        elif line.startswith("committer "):
            info["committer"] = line[10:]
    def split_ident(text: str) -> dict:
        # "name <email> 1712345678 +0800"
        # 保留原时区偏移：否则 GitHub 会按 UTC 存，commit 的 SHA 就和本地对不上，
        # 以后想 git fetch / pull --ff-only 会变成两条不同的历史。
        left, _, stamp = text.rpartition("> ")
        name, _, email = left.partition(" <")
        epoch, _, tz = stamp.partition(" ")
        import datetime
        sign = -1 if tz.startswith("-") else 1
        delta = datetime.timedelta(hours=int(tz[1:3] or 0), minutes=int(tz[3:5] or 0)) * sign
        dt = datetime.datetime.fromtimestamp(int(epoch), datetime.timezone(delta))
        return {"name": name, "email": email.rstrip(">"),
                "date": dt.strftime("%Y-%m-%dT%H:%M:%S") + dt.strftime("%z")[:3] + ":" + dt.strftime("%z")[3:]}
    return {"author": split_ident(info["author"]),
            "committer": split_ident(info["committer"]),
            "message": message}


def commit_files(sha: str) -> list[tuple[str, str, str]]:
    """返回 [(mode, blob_sha, path)]。"""
    out = git("ls-tree", "-r", sha)
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        mode, kind, blob = meta.split()
        if kind != "blob":
            continue
        files.append((mode, blob, path))
    return files


def ensure_non_empty(token: str, owner: str, repo: str) -> bool:
    """空仓库上 Git Data API 会拒绝创建 blob（409 Git Repository is empty）。

    先用 Contents API 建一个占位提交把仓库初始化，之后整条历史推上去再强推分支覆盖，
    占位提交会变成游离对象，不影响最终的历史。
    """
    try:
        api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{BRANCH}", token)
        return False
    except ApiError as exc:
        # 空仓库上这个接口返回 409（Git Repository is empty），有的版本返回 404
        if exc.status not in (404, 409):
            raise
    api("PUT", f"/repos/{owner}/{repo}/contents/.init-placeholder", token, {
        "message": "init placeholder",
        "content": base64.b64encode(b"placeholder\n").decode("ascii"),
    })
    print("空仓库：已建占位提交，稍后会被真实历史覆盖")
    return True


def push_history(token: str, owner: str, repo: str) -> str:
    shas = git("rev-list", "--reverse", "HEAD").split()
    if not shas:
        raise SystemExit("本地没有任何提交")
    print(f"本地提交 {len(shas)} 个，准备重建到 {owner}/{repo}")

    blob_cache: dict[str, str] = {}
    parent: str | None = None
    for index, sha in enumerate(shas, 1):
        meta = parse_commit(sha)
        tree = []
        for mode, blob_sha, path in commit_files(sha):
            if blob_sha not in blob_cache:
                content = git("cat-file", "blob", blob_sha, binary=True)
                created = api("POST", f"/repos/{owner}/{repo}/git/blobs", token, {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                })
                blob_cache[blob_sha] = created["sha"]
            tree.append({"path": path, "mode": mode, "type": "blob",
                         "sha": blob_cache[blob_sha]})
        new_tree = api("POST", f"/repos/{owner}/{repo}/git/trees", token,
                       {"tree": tree})["sha"]
        payload = {
            "message": meta["message"],
            "tree": new_tree,
            "parents": [parent] if parent else [],
            "author": meta["author"],
            "committer": meta["committer"],
        }
        new_commit = api("POST", f"/repos/{owner}/{repo}/git/commits", token, payload)
        parent = new_commit["sha"]
        first_line = meta["message"].splitlines()[0][:60] if meta["message"] else ""
        print(f"  [{index}/{len(shas)}] {parent[:8]} {first_line}")

    try:
        api("POST", f"/repos/{owner}/{repo}/git/refs", token,
            {"ref": f"refs/heads/{BRANCH}", "sha": parent})
    except ApiError as exc:
        if exc.status not in (409, 422):
            raise
        api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{BRANCH}", token,
            {"sha": parent, "force": True})
    return parent


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 REST API 上传仓库")
    parser.add_argument("--repo", required=True, help="仓库名")
    parser.add_argument("--owner", help="仓库归属（默认取 token 所属账号）")
    parser.add_argument("--create", action="store_true", help="先创建仓库")
    parser.add_argument("--private", action="store_true", help="建私有仓库")
    parser.add_argument("--description", default="", help="仓库描述")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要上传的内容，不发请求")
    parser.add_argument("--use-stored-credential", action="store_true",
                        help="从 git 凭据管理器取 token（不读 GH_TOKEN 环境变量）")
    args = parser.parse_args()

    if args.dry_run:
        shas = git("rev-list", "--reverse", "HEAD").split()
        print(f"提交 {len(shas)} 个：")
        for sha in shas:
            meta = parse_commit(sha)
            files = commit_files(sha)
            print(f"  {sha[:8]} {meta['message'].splitlines()[0][:50]}  文件 {len(files)} 个")
        total = len({blob for sha in shas for _, blob, _ in commit_files(sha)})
        print(f"唯一 blob {total} 个 → 需要 {total} 次 blob 请求 + {len(shas)} 次 tree/commit")
        return 0

    token = None
    source = ""
    if args.use_stored_credential:
        token = token_from_git_credential()
        source = "git 凭据管理器"
    if not token:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        source = "GH_TOKEN 环境变量"
    if not token:
        print("没拿到凭据：请设置 GH_TOKEN 环境变量，或加 --use-stored-credential")
        return 1
    print(f"凭据来源：{source}")

    me = api("GET", "/user", token)
    owner = args.owner or me["login"]
    print(f"token 所属账号：{owner}")

    if args.create:
        payload = {"name": args.repo, "private": bool(args.private)}
        if args.description:
            payload["description"] = args.description
        created = api("POST", "/user/repos", token, payload)
        print(f"已创建仓库：{created['full_name']}")
    else:
        try:
            api("GET", f"/repos/{owner}/{args.repo}", token)
        except ApiError as exc:
            if exc.status == 404:
                raise SystemExit(f"仓库 {owner}/{args.repo} 不存在，加 --create 先建") from exc
            raise
        print(f"目标仓库已存在：{owner}/{args.repo}")

    ensure_non_empty(token, owner, args.repo)

    head = push_history(token, owner, args.repo)
    print(f"\n完成：https://github.com/{owner}/{args.repo}  （HEAD {head[:8]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
