import pika
import subprocess
import json
import os
import sys
import traceback

# 從環境變數讀取 RabbitMQ 網址，預設為 localhost
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'video_tasks'

def transcode_video(input_path, output_path):
    """
    呼叫 FFmpeg 進行轉檔。
    """
    # ★ 聰明的暫存命名法：取得原始目錄與副檔名，並加上 tmp_ 前綴
    # 這樣可以讓 FFmpeg 根據副檔名自動判斷要轉換的格式 (MP4/MOV/AVI)
    dir_name = os.path.dirname(output_path)
    base_name = os.path.basename(output_path)
    temp_output_path = os.path.join(dir_name, f"tmp_{base_name}")
    
    command = [
        'ffmpeg',
        '-y',
        '-i', input_path,
        '-c:v', 'libx264',
        '-preset', 'ultrafast',  # 極速模式：用稍微差一點的壓縮率換取極低的記憶體消耗
        '-crf', '28',            # 稍微降低一點畫質以節省資源
        '-threads', '2',         # 強制限制它只能用 2 個執行緒，避免瞬間吃爆 RAM
        '-vf', 'scale=-2:720',   # 不管原片多大，全部縮小成 720p (減少處理負載)
        temp_output_path         # 先將轉檔結果輸出到暫存檔
    ]
    
    print(f"[*] 執行轉檔指令: {' '.join(command)}")
    sys.stdout.flush() # 強制立刻輸出 Log，避免被 K8s 吞掉
    
    try:
        # 讓 FFmpeg 的進度直接且即時地印在 Log 中
        process = subprocess.run(command)
        
        if process.returncode != 0:
            print(f"[!] 轉檔失敗，FFmpeg 回傳錯誤碼: {process.returncode}")
            sys.stdout.flush()
            # 如果轉檔失敗且暫存檔存在，把壞掉的半成品刪除，避免佔用硬碟空間
            if os.path.exists(temp_output_path):
                os.remove(temp_output_path)
            return False
            
        # 完全轉檔成功後，才把 tmp_ 前綴拿掉，變成正式的檔案
        os.rename(temp_output_path, output_path)
        return True
        
    except Exception as e:
        print(f"[!] 呼叫 FFmpeg 時發生嚴重例外錯誤: {str(e)}")
        traceback.print_exc() # 印出完整的錯誤追蹤路徑
        sys.stdout.flush()
        # 發生例外錯誤時也要清理暫存檔
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
        return False

def callback(ch, method, properties, body):
    """處理從 RabbitMQ 收到的訊息"""
    try:
        task = json.loads(body)
        input_file = task.get('input_file')
        output_file = task.get('output_file')

        print(f"\n[x] 收到新任務: {input_file} -> {output_file}")
        sys.stdout.flush()

        # 執行轉檔
        success = transcode_video(input_file, output_file)

        if success:
            print(" [v] 轉檔成功！")
            sys.stdout.flush()
            # 轉檔完成，回覆 ACK
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(" [!] 發生錯誤，任務未完成。")
            sys.stdout.flush()
            # 發生錯誤時，回傳 NACK 並丟棄任務
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            
    except Exception as e:
        print(f"[!] 處理 RabbitMQ 訊息時發生未預期錯誤: {str(e)}")
        sys.stdout.flush()
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

def main():
    # 1. 建立與 RabbitMQ 的連線
    connection = pika.BlockingConnection(pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        heartbeat=0  # 關閉心跳超時機制，允許長時間轉檔
    ))
    channel = connection.channel()

    # 2. 宣告佇列
    channel.queue_declare(queue=QUEUE_NAME, durable=True)

    # 3. 設定 QoS
    channel.basic_qos(prefetch_count=1)

    # 4. 開始消費訊息
    channel.basic_consume(queue=QUEUE_NAME, on_message_callback=callback)

    print(' [*] Worker 啟動成功，等待任務中。要退出請按 CTRL+C')
    sys.stdout.flush()
    channel.start_consuming()

if __name__ == '__main__':
    main()