#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键本地预览 chuan-os 文档/架构图。

背景: Trae IDE 预览面板会在 file:// 协议下注入 previewer-tools 脚本，
其内部 connection 桥接对象因 origin 为 null 无法建立，触发
"connection.on is not a function" 报错。通过本地 HTTP 服务访问
(origin 有效) 可消除该报错。

用法:
    python scripts/preview_diagrams.py               # 启动服务并自动打开浏览器
    python scripts/preview_diagrams.py --port 9000   # 指定端口
    python scripts/preview_diagrams.py --no-open     # 只启动服务，不打开浏览器
    python scripts/preview_diagrams.py --target docs/diagrams/xxx.html  # 指定页面
"""
import argparse
import os
import socket
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TARGET = "docs/diagrams/chuan-os-architecture.html"


def find_free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"端口 {start}~{start + 19} 均被占用，请用 --port 指定其它端口")


def main() -> None:
    parser = argparse.ArgumentParser(description="本地预览 chuan-os 文档/架构图")
    parser.add_argument("--port", type=int, default=8322, help="HTTP 端口（默认 8322）")
    parser.add_argument("--no-open", action="store_true", help="只启动服务，不自动打开浏览器")
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"相对项目根的页面路径（默认 {DEFAULT_TARGET}）",
    )
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)
    port = find_free_port(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", port), SimpleHTTPRequestHandler)
    url = f"http://127.0.0.1:{port}/{args.target}"

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[preview] 服务已启动: http://127.0.0.1:{port}/")
    print(f"[preview] 页面地址: {url}")
    if not args.no_open:
        webbrowser.open(url)
        print("[preview] 已在默认浏览器打开；若未弹出请手动访问上面的地址。")
    print("[preview] 按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[preview] 服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
