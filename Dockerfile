FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    QSO_DATABASE=/data/qso.db \
    QSO_PORT=8000

WORKDIR /app

# 先装依赖，利用构建缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码与脚本
COPY app ./app
COPY scripts ./scripts
RUN chmod +x scripts/init_db.sh scripts/entrypoint.sh

RUN mkdir -p /data

# 数据初始化：构建阶段即建库建表（幂等；运行时 entrypoint 对挂载卷再执行一次）
RUN scripts/init_db.sh

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

ENTRYPOINT ["scripts/entrypoint.sh"]
