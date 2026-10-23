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
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}\n{detail}") from exc
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
        left, _, stamp = text.rpartition("> ")
        name, _, email = left.partition(" <")
        epoch, _, tz = stamp.partition(" ")
        import datetime
        dt = datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc)
        return {"name": name, "email": email.rstrip(">"),
                "date": dt.strftime("%Y-%m-%dT%H:%M:%SZ")}
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

    api("POST", f"/repos/{owner}/{repo}/git/refs", token,
        {"ref": f"refs/heads/{BRANCH}", "sha": parent})
    return parent


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 REST API 上传仓库")
    parser.add_argument("--repo", required=True, help="仓库名")
    parser.add_argument("--owner", help="仓库归属（默认取 token 所属账号）")
    parser.add_argument("--create", action="store_true", help="先创建仓库")
    parser.add_argument("--private", action="store_true", help="建私有仓库")
    parser.add_argument("--description", default="", help="仓库描述")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要上传的内容，不发请求")
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

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("缺少 GH_TOKEN 环境变量（需要 repo 权限的 PAT）")
        return 1

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
        api("GET", f"/repos/{owner}/{args.repo}", token)
        print(f"目标仓库已存在：{owner}/{args.repo}")

    head = push_history(token, owner, args.repo)
    print(f"\n完成：https://github.com/{owner}/{args.repo}  （HEAD {head[:8]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
