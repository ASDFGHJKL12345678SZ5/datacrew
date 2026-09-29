# DataCrew API 镜像。
# 说明：
# - 入口是 python -m app.main（内部把 uvicorn 循环固定为 Selector，
#   Linux 默认即 Selector，Windows 开发机与容器行为一致）
# - 非 root 运行：API 服务不需要 root，泄漏面越小越好
# - 健康检查用 python urllib：slim 镜像没有 curl，不为此多装一个包
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# 先装依赖再拷代码：依赖不变时命中 Docker 层缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY eval ./eval

RUN useradd --create-home --uid 1000 datacrew && chown -R datacrew:datacrew /srv
USER datacrew

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "app.main"]
