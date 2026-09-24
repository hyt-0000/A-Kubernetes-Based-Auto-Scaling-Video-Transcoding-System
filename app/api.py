from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, FileResponse
from typing import List
import pika
import json
import os
import shutil
import uuid
import uvicorn

app = FastAPI(title="Video Transcoding API")

# K8s 環境變數設定
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'rabbitmq')
QUEUE_NAME = 'video_tasks'
SHARED_DIR = '/app/videos'  # 掛載的 PVC 目錄

@app.get("/", response_class=HTMLResponse)
async def get_upload_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Video Transcoding Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4 font-sans">
        
        <div class="max-w-md w-full bg-slate-800 rounded-2xl shadow-2xl p-8 border border-slate-700">
            <div class="text-center mb-8">
                <div class="bg-blue-500 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 shadow-lg shadow-blue-500/30">
                    <i class="fas fa-cloud-upload-alt text-2xl text-white"></i>
                </div>
                <h2 class="text-2xl font-bold text-slate-100">雲端影音轉檔系統</h2>
                <p class="text-slate-400 mt-2 text-sm">Kubernetes 自動擴展架構</p>
            </div>
            
            <form action="/upload" method="post" enctype="multipart/form-data" class="space-y-6">
                
                <div class="relative border-2 border-dashed border-slate-600 rounded-xl p-8 hover:border-blue-400 transition-colors bg-slate-800/50 group text-center cursor-pointer" onclick="document.getElementById('file-upload').click()">
                    <i class="fas fa-file-video text-4xl text-slate-500 group-hover:text-blue-400 mb-3 transition-colors"></i>
                    <p class="text-sm text-slate-300 font-medium">點擊選擇影片檔案</p>
                    <p class="text-xs text-slate-500 mt-1">支援批次上傳多個檔案</p>
                    <input id="file-upload" type="file" name="files" multiple class="hidden" onchange="updateFileName(this)">
                </div>
                
                <div id="file-list" class="hidden bg-slate-900 rounded-lg p-3 text-sm text-blue-300 text-center font-mono border border-slate-700">
                </div>

                <div class="bg-slate-900/50 p-4 rounded-xl border border-slate-700">
                    <label class="block text-sm font-medium text-slate-300 mb-2">
                        <i class="fas fa-sliders-h mr-2"></i>選擇轉檔目標格式
                    </label>
                    <select name="target_format" class="w-full bg-slate-800 border border-slate-600 text-white rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 appearance-none cursor-pointer">
                        <option value="mp4">MP4 (通用格式，適合網頁與手機)</option>
                        <option value="mov">MOV (Apple QuickTime 格式)</option>
                        <option value="avi">AVI (Windows 傳統高畫質格式)</option>
                    </select>
                </div>

                <button type="submit" class="w-full bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-bold py-3 px-4 rounded-xl shadow-lg transition-all transform hover:-translate-y-0.5 hover:shadow-blue-500/25 flex items-center justify-center gap-2">
                    <i class="fas fa-rocket"></i>
                    開始批次轉檔
                </button>
            </form>
        </div>

        <script>
            // 當使用者選擇檔案後，動態顯示選了幾個檔案
            function updateFileName(input) {
                const fileList = document.getElementById('file-list');
                if (input.files && input.files.length > 0) {
                    fileList.classList.remove('hidden');
                    fileList.innerHTML = `<i class="fas fa-check-circle mr-2"></i>已準備就緒：${input.files.length} 個檔案`;
                } else {
                    fileList.classList.add('hidden');
                    fileList.innerHTML = '';
                }
            }
        </script>
    </body>
    </html>
    """

@app.post("/upload", response_class=HTMLResponse)
async def upload_videos(
    files: List[UploadFile] = File(...),
    target_format: str = Form(...) 
):
    # 1. 確保共享目錄存在
    os.makedirs(SHARED_DIR, exist_ok=True)
    
    # 2. 建立 RabbitMQ 連線
    try:
        connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"無法連接 RabbitMQ: {str(e)}")
    
    tasks_html = "" 
    
    # 3. 迴圈處理每個上傳的檔案
    for file in files:
        unique_id = str(uuid.uuid4())[:8]
        file_ext = os.path.splitext(file.filename)[1]
        input_filename = f"input_{unique_id}{file_ext}"
        
        # 根據使用者選定的格式決定輸出的副檔名
        output_filename = f"output_{unique_id}.{target_format}" 
        
        input_path = os.path.join(SHARED_DIR, input_filename)
        output_path = os.path.join(SHARED_DIR, output_filename)
        
        # 儲存檔案到 PVC
        try:
            with open(input_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            connection.close()
            raise HTTPException(status_code=500, detail=f"儲存檔案 {file.filename} 失敗: {str(e)}")
            
        # 打包任務資訊並發送給 RabbitMQ
        task_data = {
            "input_file": input_path,
            "output_file": output_path
        }
        
        channel.basic_publish(
            exchange='',
            routing_key=QUEUE_NAME,
            body=json.dumps(task_data),
            properties=pika.BasicProperties(
                delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
            )
        )
        
        # 動態渲染每個任務狀態列 (帶上下載格式參數)
        tasks_html += f"""
        <li class="p-3 bg-slate-700/50 rounded-lg flex justify-between items-center border border-slate-600 mb-2">
            <div class="truncate pr-4 flex-1">
                <span class="text-blue-400 font-medium block truncate" title="{file.filename}">
                    <i class="fas fa-film mr-2"></i>{file.filename}
                    <span class="text-slate-500 text-xs ml-2">➔ {target_format.upper()}</span>
                </span>
                <div class="text-xs text-slate-400 mt-1">追蹤 ID: {unique_id}</div>
            </div>
            
            <div class="flex items-center gap-2">
                <span class="px-2 py-1 bg-slate-600 text-slate-300 text-xs rounded-md whitespace-nowrap">
                    已排入佇列
                </span>
                
                <a href="/download/{unique_id}?ext={target_format}" target="_blank" class="px-3 py-1 bg-blue-500 hover:bg-blue-400 text-white text-xs font-bold rounded-md shadow transition-colors whitespace-nowrap">
                    <i class="fas fa-download mr-1"></i>下載 / 檢查
                </a>
            </div>
        </li>
        """
    
    connection.close()
    
    # 組合最終的成功畫面 HTML
    success_page = f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Upload Success - Video Transcoding</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="max-w-2xl w-full bg-slate-800 rounded-2xl shadow-2xl p-8 border border-slate-700">
            
            <div class="text-center mb-8">
                <div class="bg-emerald-500 w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-4 shadow-lg shadow-emerald-500/30">
                    <i class="fas fa-check text-3xl text-white"></i>
                </div>
                <h2 class="text-2xl font-bold text-emerald-400">任務派發成功！</h2>
                <p class="text-slate-400 mt-2 text-sm">已成功將 {len(files)} 個影音檔案送入 RabbitMQ 佇列</p>
            </div>
            
            <div class="bg-slate-900/50 rounded-xl p-4 border border-slate-700 mb-8 max-h-64 overflow-y-auto custom-scrollbar">
                <ul>
                    {tasks_html}
                </ul>
            </div>
            
            <a href="/" class="block w-full bg-slate-700 hover:bg-slate-600 text-white text-center font-bold py-3 px-4 rounded-xl transition-colors border border-slate-600 hover:border-slate-500">
                <i class="fas fa-arrow-left mr-2"></i>繼續上傳其他影片
            </a>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=success_page)

@app.get("/download/{file_id}")
async def download_video(file_id: str, ext: str = "mp4"):
    # 根據要求的副檔名組合檔案名稱
    output_filename = f"output_{file_id}.{ext}"
    file_path = os.path.join(SHARED_DIR, output_filename)
    
    # 根據副檔名動態決定 MIME Type，確保瀏覽器正確下載
    media_type_map = {
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo"
    }
    target_media_type = media_type_map.get(ext, "application/octet-stream")
    
    # 檢查檔案到底轉好了沒
    if os.path.exists(file_path):
        return FileResponse(
            path=file_path, 
            filename=output_filename, 
            media_type=target_media_type
        )
    else:
        # 找不到檔案時的美化版等待畫面
        return HTMLResponse(
            content=f"""
            <div style="font-family: sans-serif; padding: 20px; text-align: center; background-color: #1e293b; color: white; height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                <h2 style="color: #60a5fa; margin-bottom: 16px;">⏳ 影片還在轉檔中，或是任務失敗了！</h2>
                <p>Worker 正在將影片轉換為 {ext.upper()} 格式，請稍等一下再重新整理此頁面。</p>
                <p style="color: #94a3b8; font-size: 0.8em; margin-top: 8px;">尋找的檔案: {output_filename}</p>
                <button onclick="window.history.back()" style="margin-top: 20px; padding: 10px 20px; cursor: pointer; background-color: #3b82f6; color: white; border: none; border-radius: 8px; font-weight: bold;">回上一頁</button>
            </div>
            """, 
            status_code=404
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)