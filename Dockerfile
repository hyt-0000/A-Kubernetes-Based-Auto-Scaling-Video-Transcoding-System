# 1. 使用輕量級的 Python 3.10 作為基底 (與你目前的版本一致)
FROM python:3.10-slim

# 2. 設定容器內的工作目錄
WORKDIR /app

# 3. 更新 Linux 套件清單，並安裝 FFmpeg
# (這樣你就不會再遇到找不到 ffmpeg 的問題了！)
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 4. ★ 關鍵修改：從 app/ 目錄複製 requirements.txt
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. ★ 關鍵修改：從 app/ 目錄複製 Worker 程式碼
COPY app/worker.py .

# 6. 當容器啟動時，預設執行的指令
CMD ["python", "worker.py"]